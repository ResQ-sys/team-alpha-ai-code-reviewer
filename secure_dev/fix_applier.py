"""Batch application of review-suggested fixes with snapshot-based safety.

This module turns a list of ``suggested_fixes`` (as produced by
``review_generation_agent`` / ``verifier_agent``) into real edits on disk. It
is deliberately conservative:

- **Dry-run first (default):** every candidate fix is *matched against the live
  file* and a real unified diff is computed, but nothing is written. A fix whose
  ``original_code`` cannot be located is surfaced as *skipped* with a reason
  rather than silently dropped.
- **Filters:** ``only_verified`` (default) drops fixes the verifier flagged;
  ``only_approved`` drops fixes whose key is not in ``approved_keys``.
- **Reversible real apply:** before writing anything the current contents of
  every target file are snapshotted; on *any* exception the snapshot is restored
  so a partial failure never leaves the tree half-patched.
- **Atomic-ish commit:** when the target is a git work tree and at least one fix
  landed, the batch is committed with a descriptive message.

Public contract (consumed by ``server.api`` and ``secure_dev.repair_loop``)::

    @dataclass ApplyResult(applied, skipped, combined_diff, commit, dry_run)
    apply_fixes(repo_path, suggested_fixes, *, dry_run=True,
                only_verified=True, only_approved=False,
                approved_keys=None) -> ApplyResult
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from secure_dev import git_ops, patch
from secure_dev.safe_path import PathEscapeError, resolve_within

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Outcome of applying a batch of fixes.

    ``applied`` and ``skipped`` are lists of plain dicts (JSON-serializable so
    they can be persisted and returned over the API). ``combined_diff`` is the
    concatenation of every per-fix unified diff (of the fixes that were applied,
    or that *would* be applied in a dry run). ``commit`` is the resulting git
    sha, or ``None`` for a dry run / non-git repo / nothing-to-commit.
    """

    applied: list[dict]
    skipped: list[dict]
    combined_diff: str
    commit: Optional[str]
    dry_run: bool


def _fix_key(fix: dict) -> str:
    """Return a stable key identifying *fix* for approval matching.

    Prefers the ``finding_ref`` (``file:line:rule_id``) emitted by the review
    generator; falls back to a composed key so a fix without a ref can still be
    approved deterministically.
    """
    ref = fix.get("finding_ref")
    if ref:
        return str(ref)
    return f"{fix.get('file', '')}:{fix.get('line', 0)}:{_rule_id(fix)}"


def _rule_id(fix: dict) -> str:
    """Best-effort rule identifier for commit messages and reporting."""
    if fix.get("rule_id"):
        return str(fix["rule_id"])
    ref = fix.get("finding_ref", "")
    if ref and ":" in ref:
        return ref.rsplit(":", 1)[-1]
    return "unknown-rule"


def _skip(fix: dict, reason: str) -> dict:
    """Build a JSON-serializable skipped-fix record."""
    return {
        "file": fix.get("file"),
        "line": fix.get("line", 0),
        "rule_id": _rule_id(fix),
        "key": _fix_key(fix),
        "reason": reason,
    }


def _filter_reason(
    fix: dict,
    *,
    only_verified: bool,
    only_approved: bool,
    approved_keys: set,
) -> Optional[str]:
    """Return a skip reason if *fix* fails a precondition/filter, else ``None``."""
    fixed = (fix.get("fixed_code") or "").strip()
    if not fixed:
        return "no fixed_code provided"
    if not (fix.get("original_code") or "").strip():
        return "no original_code to locate"
    if fixed == (fix.get("original_code") or "").strip():
        return "fixed_code identical to original_code"
    if only_verified and not fix.get("verified"):
        return "fix not verified"
    if only_approved and _fix_key(fix) not in approved_keys:
        return "fix not approved"
    return None


def _repo_relative(repo_path: str, file: str) -> str:
    """Normalize a finding's ``file`` to a path relative to *repo_path*.

    Pipeline findings carry paths relative to the review's working directory,
    which typically already include the repo directory itself (e.g.
    ``./sample_repo/app.py`` or ``sample_repo/app.py`` for a repo at
    ``./sample_repo``). Joining those onto ``repo_path`` again double-prefixes
    the path. This resolves the finding to a genuine repo-relative path in a
    CWD-independent way, trying candidate spellings and keeping the first whose
    join with ``repo_path`` actually exists. Falls back to the raw value (which
    the caller then reports as *not found*) when nothing matches.
    """
    repo_base = os.path.basename(os.path.normpath(repo_path))
    candidates = [file]
    if file.startswith("./"):
        candidates.append(file[2:])
    for prefix in (f"{repo_base}/", f"./{repo_base}/"):
        if file.startswith(prefix):
            candidates.append(file[len(prefix):])
    candidates.extend(os.path.normpath(c) for c in list(candidates))

    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(os.path.join(repo_path, cand)):
            return cand
    return file


def _plan_fix(repo_path: str, fix: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Match a single fix against the live file without writing.

    Returns ``(planned, skipped)`` where exactly one is non-None. ``planned``
    carries the relative file, computed unified diff, and metadata for a fix
    that *can* be applied; ``skipped`` explains why it cannot.
    """
    raw_file = fix.get("file")
    if not raw_file:
        return None, _skip(fix, "fix has no file")
    rel_file = _repo_relative(repo_path, raw_file)

    try:
        abs_path = resolve_within(repo_path, rel_file)
    except PathEscapeError as exc:
        return None, _skip(fix, f"unsafe path rejected: {exc}")
    if not os.path.isfile(abs_path):
        return None, _skip(fix, f"file not found: {rel_file}")

    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, _skip(fix, f"could not read file: {exc}")

    new_text, replaced = patch.locate_and_replace(
        text, fix.get("original_code", ""), fix.get("fixed_code", "")
    )
    if not replaced:
        return None, _skip(fix, "original_code not found in file")
    if new_text == text:
        return None, _skip(fix, "fix produced no change")

    diff = patch.make_unified_diff(rel_file, text, new_text)
    planned = {
        "file": rel_file,
        "line": fix.get("line", 0),
        "rule_id": _rule_id(fix),
        "severity": fix.get("severity"),
        "key": _fix_key(fix),
        "verified": bool(fix.get("verified")),
        "diff": diff,
        "_fix": fix,
    }
    return planned, None


def _commit_message(applied: list[dict]) -> str:
    """Compose a conventional-commit message for the applied batch."""
    if len(applied) == 1:
        entry = applied[0]
        return (
            f"fix(security): {entry['rule_id']} in "
            f"{entry['file']}:{entry['line']}"
        )
    rules = sorted({entry["rule_id"] for entry in applied})
    summary = ", ".join(rules[:3]) + ("..." if len(rules) > 3 else "")
    return f"fix(security): apply {len(applied)} automated fixes ({summary})"


def _apply_planned(repo_path: str, planned: list[dict]) -> tuple[list[dict], list[dict]]:
    """Write every planned fix to disk under snapshot protection.

    Snapshots all target files up front and restores them if any unexpected
    exception is raised mid-batch, so the working tree is never left partially
    patched. Individual match failures (a file changing underfoot) are recorded
    as skips rather than aborting the batch.
    """
    target_files = sorted({entry["file"] for entry in planned})
    backup = git_ops.snapshot_backup(repo_path, target_files)

    applied: list[dict] = []
    skipped: list[dict] = []
    try:
        for entry in planned:
            fix = entry["_fix"]
            result = patch.apply_fix_to_file(
                repo_path,
                entry["file"],
                fix.get("original_code", ""),
                fix.get("fixed_code", ""),
            )
            if result.applied:
                applied.append(
                    {
                        "file": entry["file"],
                        "line": result.line or entry["line"],
                        "rule_id": entry["rule_id"],
                        "severity": entry["severity"],
                        "key": entry["key"],
                        "verified": entry["verified"],
                        "diff": result.diff,
                    }
                )
            else:
                skipped.append(_skip(fix, result.error or "apply failed"))
    except Exception:
        # Roll the tree back to its pre-apply state before propagating.
        logger.exception("apply batch failed; restoring snapshot")
        git_ops.restore_backup(repo_path, backup)
        raise
    return applied, skipped


def apply_fixes(
    repo_path: str,
    suggested_fixes: list[dict],
    *,
    dry_run: bool = True,
    only_verified: bool = True,
    only_approved: bool = False,
    approved_keys: Optional[set] = None,
) -> ApplyResult:
    """Apply (or preview) a batch of suggested fixes to *repo_path*.

    Each fix is first filtered (verified/approved/well-formed), then matched
    against the live file to produce a real unified diff. In dry-run mode
    nothing is written. In real mode the matched fixes are applied under
    snapshot protection and, on a git work tree, committed as one batch.

    Returns an :class:`ApplyResult`; never raises for an individual bad fix
    (those become skips). Raises ``ValueError`` only for an unusable repo path.
    """
    if not repo_path or not os.path.isdir(repo_path):
        raise ValueError(f"repo_path is not a directory: {repo_path!r}")

    keys = set(approved_keys) if approved_keys is not None else set()
    fixes = suggested_fixes or []

    planned: list[dict] = []
    skipped: list[dict] = []
    for fix in fixes:
        reason = _filter_reason(
            fix,
            only_verified=only_verified,
            only_approved=only_approved,
            approved_keys=keys,
        )
        if reason is not None:
            skipped.append(_skip(fix, reason))
            continue
        plan, skip = _plan_fix(repo_path, fix)
        if plan is not None:
            planned.append(plan)
        else:
            skipped.append(skip)

    if dry_run:
        combined = "".join(entry["diff"] for entry in planned)
        preview = [
            {k: v for k, v in entry.items() if k != "_fix"} for entry in planned
        ]
        logger.info(
            "apply_fixes dry-run: %d planned, %d skipped", len(preview), len(skipped)
        )
        return ApplyResult(
            applied=preview,
            skipped=skipped,
            combined_diff=combined,
            commit=None,
            dry_run=True,
        )

    applied, apply_skips = _apply_planned(repo_path, planned)
    skipped.extend(apply_skips)

    commit: Optional[str] = None
    if applied and git_ops.ensure_git_repo(repo_path):
        # Stage/commit ONLY the files we actually patched, so a dirty target repo
        # never has unrelated changes swept into the automated security commit.
        applied_files = sorted({entry["file"] for entry in applied})
        commit = git_ops.commit_all(
            repo_path, _commit_message(applied), files=applied_files
        )

    combined = "".join(entry["diff"] for entry in applied)
    logger.info(
        "apply_fixes applied %d, skipped %d, commit=%s",
        len(applied),
        len(skipped),
        commit,
    )
    return ApplyResult(
        applied=applied,
        skipped=skipped,
        combined_diff=combined,
        commit=commit,
        dry_run=False,
    )
