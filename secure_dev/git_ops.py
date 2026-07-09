"""Timeout-guarded git helpers plus in-memory snapshot/restore.

Every git invocation goes through :func:`_run_git`, which always sets a
timeout and never raises on a non-zero exit code (callers inspect the result
explicitly). All functions degrade gracefully when ``repo`` is not a git work
tree so the fix-application pipeline can still run on a plain directory.

Public contract (consumed by ``secure_dev.fix_applier`` and
``secure_dev.repair_loop``)::

    ensure_git_repo(path) -> bool
    ensure_branch(repo, name) -> None
    working_tree_clean(repo) -> bool
    commit_all(repo, message, files=None) -> str | None
    snapshot_backup(repo, files) -> dict
    restore_backup(repo, backup) -> None
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from secure_dev.safe_path import PathEscapeError, resolve_within

# Bound every git call so a hung index lock or credential prompt can never
# wedge the pipeline.
_GIT_TIMEOUT_SECONDS = 30

# Fallback identity used only when the repo (and global git config) has no
# committer identity, so automated commits never fail with
# "Please tell me who you are".
_FALLBACK_NAME = "ai-code-reviewer"
_FALLBACK_EMAIL = "ai-code-reviewer@localhost"


def _run_git(repo: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run ``git <args>`` inside *repo* with a timeout, never raising on exit code.

    On timeout or a missing git binary a synthetic failed CompletedProcess is
    returned (returncode 1) so callers can treat every failure uniformly
    instead of catching exceptions at every call site.
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=1, stdout="", stderr=str(exc)
        )


def ensure_git_repo(path: str) -> bool:
    """Return True when *path* is inside a git work tree."""
    if not path or not os.path.isdir(path):
        return False
    result = _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _branch_exists(repo: str, name: str) -> bool:
    """Return True when a local branch *name* already exists."""
    result = _run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
    return result.returncode == 0


def ensure_branch(repo: str, name: str) -> None:
    """Create (if missing) and check out branch *name*.

    No-op when *repo* is not a git work tree so callers need not branch on
    repo type. Errors are swallowed intentionally: branch management is a
    convenience, and a failure here must not abort a fix run.
    """
    if not name or not ensure_git_repo(repo):
        return
    if _branch_exists(repo, name):
        _run_git(repo, ["checkout", name])
    else:
        _run_git(repo, ["checkout", "-b", name])


def working_tree_clean(repo: str) -> bool:
    """Return True when the work tree has no staged or unstaged changes.

    A non-git directory is treated as clean (there is nothing git-tracked to
    be dirty), letting the caller proceed with snapshot-based safety instead.
    """
    if not ensure_git_repo(repo):
        return True
    result = _run_git(repo, ["status", "--porcelain"])
    if result.returncode != 0:
        return False
    return result.stdout.strip() == ""


def _has_identity(repo: str) -> bool:
    """Return True when a committer identity is configured for *repo*."""
    name = _run_git(repo, ["config", "user.name"])
    email = _run_git(repo, ["config", "user.email"])
    return bool(name.stdout.strip()) and bool(email.stdout.strip())


def commit_all(
    repo: str, message: str, files: Optional[list[str]] = None
) -> Optional[str]:
    """Stage changes and commit with *message*; return the commit sha.

    When *files* is provided, ONLY those paths are staged (``git add -- <files>``)
    so the commit is scoped to exactly the files that were patched — unrelated
    staged/unstaged/untracked changes in a dirty target repo are never swept into
    an automated security commit. When *files* is ``None`` the legacy behaviour
    (``git add -A``) is preserved for callers that intend a whole-tree commit.

    Returns ``None`` when *repo* is not a git repo, when there is nothing to
    commit, or when the commit fails for any reason. A fallback identity is
    injected only when the repo has none, so automated commits never fail on
    an unconfigured machine while respecting an existing configured identity.
    """
    if not ensure_git_repo(repo):
        return None

    if files is None:
        add = _run_git(repo, ["add", "-A"])
    else:
        # Stage only the explicitly named files. An empty list means nothing to
        # stage -> nothing to commit.
        if not files:
            return None
        add = _run_git(repo, ["add", "--", *files])
    if add.returncode != 0:
        return None

    # Nothing staged -> nothing to commit. Scope the check to our pathspec so
    # unrelated pre-staged changes in a dirty repo do not trigger a commit.
    if files is None:
        if working_tree_clean(repo):
            return None
    else:
        staged = _run_git(repo, ["diff", "--cached", "--name-only", "--", *files])
        if staged.returncode != 0 or not staged.stdout.strip():
            return None

    commit_args: list[str] = []
    if not _has_identity(repo):
        commit_args += [
            "-c",
            f"user.name={_FALLBACK_NAME}",
            "-c",
            f"user.email={_FALLBACK_EMAIL}",
        ]
    commit_args += ["commit", "-m", message]
    # Restrict the commit to our pathspec so anything else the user had staged is
    # left untouched by the automated security commit.
    if files is not None:
        commit_args += ["--", *files]

    commit = _run_git(repo, commit_args)
    if commit.returncode != 0:
        return None

    head = _run_git(repo, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        return None
    return head.stdout.strip() or None


def snapshot_backup(repo: str, files: list[str]) -> dict:
    """Capture the current contents of *files* (relative to *repo*) for rollback.

    Returns a mapping ``{rel_file: original_text_or_None}``. ``None`` marks a
    file that does not currently exist, so :func:`restore_backup` can delete a
    newly-created file to undo it. Unreadable files are stored as ``None`` as
    well, which is the conservative "did not have known contents" signal.
    """
    backup: dict[str, Optional[str]] = {}
    for rel in files:
        try:
            abs_path = resolve_within(repo, rel)
        except PathEscapeError:
            # An unsafe path is never written by the guarded apply layer, so
            # there is nothing to snapshot or roll back for it. Skip it rather
            # than recording it (which would let restore_backup act on it).
            continue
        if not os.path.isfile(abs_path):
            backup[rel] = None
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as handle:
                backup[rel] = handle.read()
        except (OSError, UnicodeDecodeError):
            backup[rel] = None
    return backup


def restore_backup(repo: str, backup: dict) -> None:
    """Restore files captured by :func:`snapshot_backup`.

    A ``None`` value means the file did not exist at snapshot time, so it is
    removed to fully undo a creation. Individual restore failures are collected
    and re-raised at the end so a single bad path cannot leave the rest of the
    rollback unapplied.
    """
    errors: list[str] = []
    for rel, original in backup.items():
        try:
            abs_path = resolve_within(repo, rel)
        except PathEscapeError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        try:
            if original is None:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                continue
            os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as handle:
                handle.write(original)
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
    if errors:
        raise OSError("restore_backup failed for: " + "; ".join(errors))
