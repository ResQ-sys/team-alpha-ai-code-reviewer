"""Self-healing repair loop for a single security finding.

Given one finding and a candidate fix, this module verifies the fix in a
throwaway copy of the repository before anyone touches the real tree:

1. Copy the repo to a temp directory (``shutil.copytree``) — the ORIGINAL is
   never modified here.
2. Apply the candidate ``fixed_code`` to the copied file.
3. Regression check: re-run ``utils.semgrep_runner.run_semgrep`` scoped to the
   changed file and confirm the finding's ``rule_id`` no longer fires.
4. Optionally run the repo's ``pytest`` suite (short timeout) to confirm the
   change did not break anything.
5. If the finding still fires or tests fail, ask an injected ``llm`` (via
   ``complete_schema``) for an improved ``fixed_code`` and retry, up to
   ``max_attempts`` times.

The ``llm`` dependency is injected so tests can mock it; semgrep is referenced
through the module-level ``run_semgrep`` name so tests can monkeypatch it. No
real scan, LLM call, or network access is required to exercise this module.

Public contract (consumed by the orchestration / API layer)::

    @dataclass RepairResult(finding, fixed_code, attempts, resolved,
                            regressed, test_ok, notes)
    repair_finding(repo_path, finding, fix, *, max_attempts=3,
                   run_tests=True, llm=None) -> RepairResult
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from observability.logging_setup import get_logger
from secure_dev import git_ops, patch
from utils.llm_client import LLMClient

# Imported by name (not ``import module``) so tests can
# ``monkeypatch.setattr(repair_loop, "run_semgrep", fake)`` without patching
# the shared semgrep module used elsewhere.
from utils.semgrep_runner import run_semgrep

logger = get_logger(__name__)

# Bound every subprocess so a stuck scan or hung test run can never wedge the
# repair loop.
_SEMGREP_TIMEOUT_SECONDS = 120
_PYTEST_TIMEOUT_SECONDS = 120

# Directories that are pointless (and expensive) to copy into the scratch tree.
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "*.pyc",
)

# JSON-schema-ish contract for the LLM repair request; only ``required`` is
# enforced by ``LLMClient.complete_schema``.
_FIX_SCHEMA = {
    "required": ["fixed_code"],
    "properties": {
        "fixed_code": {"type": "string"},
        "explanation": {"type": "string"},
    },
}


@dataclass
class RepairResult:
    """Outcome of attempting to self-heal one finding."""

    finding: Dict[str, Any]
    fixed_code: Optional[str]
    attempts: int
    resolved: bool
    regressed: bool
    test_ok: Optional[bool]
    notes: List[str] = field(default_factory=list)


def _rel_target(finding: Dict[str, Any], fix: Dict[str, Any]) -> Optional[str]:
    """Return the repo-relative file the fix applies to, or None if unknown."""
    rel = fix.get("file") or finding.get("file")
    if not rel:
        return None
    # Normalize any accidental leading separators so os.path.join stays inside
    # the temp repo.
    return str(rel).lstrip("/\\")


def _copy_repo(repo_path: str) -> Tuple[str, str]:
    """Copy *repo_path* into a fresh temp dir. Returns (base_dir, repo_copy)."""
    base = tempfile.mkdtemp(prefix="repair_")
    repo_copy = os.path.join(base, "repo")
    shutil.copytree(repo_path, repo_copy, ignore=_COPY_IGNORE)
    return base, repo_copy


def _repo_has_tests(repo: str) -> bool:
    """Return True when the tree contains at least one pytest-style test file."""
    for root, _dirs, files in os.walk(repo):
        for name in files:
            if name.endswith(".py") and (
                name.startswith("test_") or name.endswith("_test.py")
            ):
                return True
    return False


def _run_pytest(repo: str) -> Optional[bool]:
    """Run ``pytest -q`` in *repo*.

    Returns True when the suite passes, False when it fails, and None when no
    tests are present (or pytest could not be launched) — the "inconclusive"
    signal, which never blocks a repair on its own.
    """
    if not _repo_has_tests(repo):
        return None
    try:
        proc = subprocess.run(
            ["pytest", "-q", "-p", "no:cacheprovider"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=_PYTEST_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("pytest could not be run in repair sandbox: %s", exc)
        return None
    # pytest exit code 5 == "no tests collected"; treat as inconclusive.
    if proc.returncode == 5:
        return None
    return proc.returncode == 0


def _rule_still_fires(
    repo: str, rel_file: str, rule_id: str
) -> Tuple[bool, List[str]]:
    """Re-scan *rel_file* and report whether *rule_id* still fires.

    Returns ``(still_fires, errors)``. When ``rule_id`` is empty we cannot make
    a rule-level judgement, so we conservatively report "does not fire" and let
    the caller fall back to apply-level success.
    """
    scoped = os.path.join(repo, rel_file)
    findings, errors = run_semgrep(scoped, timeout=_SEMGREP_TIMEOUT_SECONDS)
    if not rule_id:
        return False, errors
    still = any(finding.get("rule_id") == rule_id for finding in findings)
    return still, errors


def _ask_llm_for_fix(
    llm: LLMClient,
    finding: Dict[str, Any],
    fix: Dict[str, Any],
    prior_fixed_code: Optional[str],
    failure_reason: str,
) -> Optional[str]:
    """Ask the LLM for an improved ``fixed_code``; return it or None.

    None is returned when the model produces no parseable JSON or omits the
    required key, so the caller can stop retrying instead of looping blindly.
    """
    rule_id = finding.get("rule_id", "")
    cwe = ", ".join(finding.get("cwe", []) or []) or "unspecified"
    system = (
        "You are a secure-coding repair agent. You produce a corrected code "
        "snippet that resolves a static-analysis finding WITHOUT changing "
        "unrelated behavior. Return only the replacement for the original "
        "snippet."
    )
    prompt = (
        f"Security finding rule_id: {rule_id}\n"
        f"CWE: {cwe}\n"
        f"File: {fix.get('file') or finding.get('file')}\n"
        f"Message: {finding.get('message', '')}\n\n"
        f"Original vulnerable code:\n{fix.get('original_code', '')}\n\n"
        f"Previously attempted fix (rejected):\n{prior_fixed_code or '(none)'}\n\n"
        f"Why it was rejected: {failure_reason}\n\n"
        "Provide an improved replacement as JSON with a 'fixed_code' string "
        "field that fully remediates the finding."
    )
    result = llm.complete_schema(system, prompt, _FIX_SCHEMA)
    if result.get("_parse_error"):
        return None
    improved = result.get("fixed_code")
    if not isinstance(improved, str) or not improved.strip():
        return None
    return improved


def _evaluate_candidate(
    repo_copy: str,
    rel_file: str,
    original_code: str,
    candidate: str,
    rule_id: str,
    run_tests: bool,
    backup: Dict[str, Any],
) -> Tuple[bool, bool, Optional[bool], str]:
    """Apply *candidate* to a pristine copy and evaluate it.

    Restores the target file to its snapshot first so successive attempts never
    stack on each other. Returns
    ``(applied, regressed, test_ok, note)``.
    """
    git_ops.restore_backup(repo_copy, backup)
    applied = patch.apply_fix_to_file(repo_copy, rel_file, original_code, candidate)
    if not applied.applied:
        return False, True, None, f"could not apply fix: {applied.error}"

    still_fires, scan_errors = _rule_still_fires(repo_copy, rel_file, rule_id)
    if still_fires:
        note = f"finding {rule_id} still fires after fix"
        if scan_errors:
            note += f" (scan notes: {'; '.join(scan_errors)[:200]})"
        return True, True, None, note

    test_ok: Optional[bool] = None
    if run_tests:
        test_ok = _run_pytest(repo_copy)
        if test_ok is False:
            return True, False, False, "fix cleared the finding but tests failed"

    return True, False, test_ok, "fix resolved the finding"


def repair_finding(
    repo_path: str,
    finding: Dict[str, Any],
    fix: Dict[str, Any],
    *,
    max_attempts: int = 3,
    run_tests: bool = True,
    llm: Optional[LLMClient] = None,
) -> RepairResult:
    """Verify and self-heal a candidate *fix* for *finding* in a temp copy.

    Args:
        repo_path: Path to the real repository (never mutated by this call).
        finding: The security finding dict (needs ``file``, ``rule_id``; may
            carry ``cwe``, ``message``).
        fix: Candidate fix dict (needs ``original_code``; ``fixed_code`` is the
            starting candidate — if absent, the LLM is asked to generate one).
        max_attempts: Maximum apply/scan/repair cycles (>= 1).
        run_tests: When True, run the repo's pytest suite in the sandbox.
        llm: Injected LLM client (defaults to a real ``LLMClient``). Only used
            to improve a rejected fix.

    Returns:
        A :class:`RepairResult` describing the final outcome.
    """
    notes: List[str] = []
    max_attempts = max(1, max_attempts)
    llm = llm or LLMClient()

    rel_file = _rel_target(finding, fix)
    if rel_file is None:
        notes.append("no target file on finding/fix; cannot repair")
        return RepairResult(finding, None, 0, False, False, None, notes)

    original_code = fix.get("original_code", "")
    if not original_code:
        notes.append("fix has no original_code to locate; cannot repair")
        return RepairResult(finding, None, 0, False, False, None, notes)

    candidate = fix.get("fixed_code")
    rule_id = finding.get("rule_id", "")

    if not os.path.isdir(repo_path):
        notes.append(f"repo_path is not a directory: {repo_path}")
        return RepairResult(finding, candidate, 0, False, False, None, notes)

    base_dir, repo_copy = _copy_repo(repo_path)
    # Snapshot the single target file so each attempt starts from the pristine
    # original inside the sandbox.
    backup = git_ops.snapshot_backup(repo_copy, [rel_file])

    attempts = 0
    regressed = False
    test_ok: Optional[bool] = None
    try:
        while attempts < max_attempts:
            attempts += 1

            if not candidate or not candidate.strip():
                candidate = _ask_llm_for_fix(
                    llm, finding, fix, None, "no candidate fix was provided"
                )
                if candidate is None:
                    notes.append(
                        f"attempt {attempts}: no candidate fix available from LLM"
                    )
                    regressed = True
                    break

            applied, regressed, test_ok, note = _evaluate_candidate(
                repo_copy, rel_file, original_code, candidate,
                rule_id, run_tests, backup,
            )
            notes.append(f"attempt {attempts}: {note}")

            if applied and not regressed and test_ok is not False:
                return RepairResult(
                    finding, candidate, attempts, True, False, test_ok, notes
                )

            # Rejected: ask the LLM for an improved fix for the next round.
            if attempts < max_attempts:
                improved = _ask_llm_for_fix(llm, finding, fix, candidate, note)
                if improved is None:
                    notes.append(
                        f"attempt {attempts}: LLM produced no improved fix; stopping"
                    )
                    break
                candidate = improved

        return RepairResult(
            finding, candidate, attempts, False, regressed, test_ok, notes
        )
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)
