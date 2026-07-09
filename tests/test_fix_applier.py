"""Unit tests for ``secure_dev.fix_applier``.

All tests operate on real, throwaway git repos under ``tmp_path``; none touch
the project repo, the network, or any LLM. They assert the safety contract:
dry-run writes nothing, real apply modifies the file and commits, filters drop
the right fixes, and an unlocatable ``original_code`` is skipped with a reason.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secure_dev import fix_applier, git_ops  # noqa: E402

VULN_SOURCE = (
    "import hashlib\n"
    "\n"
    "def hash_pw(pw):\n"
    "    return hashlib.md5(pw.encode()).hexdigest()\n"
)
ORIGINAL_SNIPPET = "    return hashlib.md5(pw.encode()).hexdigest()"
FIXED_SNIPPET = "    return hashlib.sha256(pw.encode()).hexdigest()"


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _make_fix(**overrides) -> dict:
    """A well-formed, verified suggested_fix targeting ``app.py``."""
    fix = {
        "file": "app.py",
        "line": 4,
        "finding_ref": "app.py:4:python.lang.security.audit.md5",
        "original_code": ORIGINAL_SNIPPET,
        "fixed_code": FIXED_SNIPPET,
        "explanation": "MD5 is weak; use SHA-256.",
        "verified": True,
        "verification_notes": "",
    }
    fix.update(overrides)
    return fix


@pytest.fixture()
def git_repo(tmp_path):
    """A real git repo with a committed vulnerable file and clean tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "tester@example.com")
    _git(str(repo), "config", "user.name", "Tester")
    (repo / "app.py").write_text(VULN_SOURCE, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-m", "initial")
    return str(repo)


@pytest.fixture()
def plain_repo(tmp_path):
    """A non-git directory containing the vulnerable file."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_text(VULN_SOURCE, encoding="utf-8")
    return str(plain)


def _read_app(repo: str) -> str:
    with open(os.path.join(repo, "app.py"), "r", encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------

def test_dry_run_produces_diff_and_writes_nothing(git_repo):
    before = _read_app(git_repo)
    result = fix_applier.apply_fixes(git_repo, [_make_fix()], dry_run=True)

    assert result.dry_run is True
    assert result.commit is None
    assert len(result.applied) == 1
    assert result.skipped == []
    assert "hashlib.sha256" in result.combined_diff
    assert result.combined_diff.startswith("--- a/app.py")
    # Nothing on disk changed.
    assert _read_app(git_repo) == before
    assert git_ops.working_tree_clean(git_repo) is True


def test_dry_run_preview_has_no_internal_fix_key(git_repo):
    result = fix_applier.apply_fixes(git_repo, [_make_fix()], dry_run=True)
    assert "_fix" not in result.applied[0]
    assert result.applied[0]["rule_id"] == "python.lang.security.audit.md5"


# --------------------------------------------------------------------------
# real apply
# --------------------------------------------------------------------------

def test_real_apply_modifies_file_and_commits(git_repo):
    head_before = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    result = fix_applier.apply_fixes(git_repo, [_make_fix()], dry_run=False)

    assert result.dry_run is False
    assert len(result.applied) == 1
    assert result.skipped == []
    # File actually changed.
    contents = _read_app(git_repo)
    assert "hashlib.sha256" in contents
    assert "hashlib.md5" not in contents
    # A new commit exists and the tree is clean.
    assert result.commit is not None
    head_after = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before
    assert head_after == result.commit
    assert git_ops.working_tree_clean(git_repo) is True
    msg = _git(git_repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert "fix(security)" in msg


def test_real_apply_on_plain_dir_writes_without_commit(plain_repo):
    result = fix_applier.apply_fixes(plain_repo, [_make_fix()], dry_run=False)
    assert result.commit is None
    assert len(result.applied) == 1
    assert "hashlib.sha256" in _read_app(plain_repo)


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------

def test_only_verified_skips_unverified_fix(git_repo):
    fix = _make_fix(verified=False)
    result = fix_applier.apply_fixes(
        git_repo, [fix], dry_run=True, only_verified=True
    )
    assert result.applied == []
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "fix not verified"


def test_only_verified_false_allows_unverified_fix(git_repo):
    fix = _make_fix(verified=False)
    result = fix_applier.apply_fixes(
        git_repo, [fix], dry_run=True, only_verified=False
    )
    assert len(result.applied) == 1


def test_only_approved_filters_by_key(git_repo):
    fix = _make_fix()
    key = fix["finding_ref"]

    approved = fix_applier.apply_fixes(
        git_repo, [fix], dry_run=True, only_approved=True, approved_keys={key}
    )
    assert len(approved.applied) == 1

    rejected = fix_applier.apply_fixes(
        git_repo, [fix], dry_run=True, only_approved=True, approved_keys=set()
    )
    assert rejected.applied == []
    assert rejected.skipped[0]["reason"] == "fix not approved"


def test_only_approved_with_none_keys_skips_all(git_repo):
    result = fix_applier.apply_fixes(
        git_repo, [_make_fix()], dry_run=True, only_approved=True, approved_keys=None
    )
    assert result.applied == []
    assert result.skipped[0]["reason"] == "fix not approved"


# --------------------------------------------------------------------------
# skips / malformed fixes
# --------------------------------------------------------------------------

def test_unlocatable_original_code_is_skipped_with_reason(git_repo):
    fix = _make_fix(original_code="    return nonexistent_call()")
    result = fix_applier.apply_fixes(git_repo, [fix], dry_run=False)
    assert result.applied == []
    assert result.skipped[0]["reason"] == "original_code not found in file"
    # Untouched.
    assert "hashlib.md5" in _read_app(git_repo)
    assert git_ops.working_tree_clean(git_repo) is True


def test_missing_fixed_code_is_skipped(git_repo):
    result = fix_applier.apply_fixes(
        git_repo, [_make_fix(fixed_code="")], dry_run=True
    )
    assert result.skipped[0]["reason"] == "no fixed_code provided"


def test_missing_file_on_disk_is_skipped(git_repo):
    result = fix_applier.apply_fixes(
        git_repo, [_make_fix(file="does_not_exist.py")], dry_run=True
    )
    assert result.applied == []
    assert "file not found" in result.skipped[0]["reason"]


def test_identical_fix_is_skipped(git_repo):
    result = fix_applier.apply_fixes(
        git_repo, [_make_fix(fixed_code=ORIGINAL_SNIPPET)], dry_run=True
    )
    assert result.skipped[0]["reason"] == "fixed_code identical to original_code"


def test_empty_fix_list_returns_empty_result(git_repo):
    result = fix_applier.apply_fixes(git_repo, [], dry_run=False)
    assert result.applied == []
    assert result.skipped == []
    assert result.combined_diff == ""
    assert result.commit is None


def test_bad_repo_path_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        fix_applier.apply_fixes(str(tmp_path / "nope"), [_make_fix()])


# --------------------------------------------------------------------------
# multi-fix batching
# --------------------------------------------------------------------------

def test_exception_mid_batch_restores_snapshot(git_repo, monkeypatch):
    """A crash while applying must roll the whole tree back to pre-apply state."""
    before = _read_app(git_repo)
    calls = {"n": 0}
    real_apply = fix_applier.patch.apply_fix_to_file

    def flaky_apply(repo, rel_file, original, fixed):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_apply(repo, rel_file, original, fixed)

    monkeypatch.setattr(fix_applier.patch, "apply_fix_to_file", flaky_apply)

    fix2 = _make_fix(
        line=3,
        finding_ref="app.py:3:python.lang.security.other",
        original_code="def hash_pw(pw):",
        fixed_code="def hash_pw(pw: str) -> str:",
    )
    with pytest.raises(RuntimeError):
        fix_applier.apply_fixes(git_repo, [_make_fix(), fix2], dry_run=False)

    # The first fix was applied then rolled back; file matches the snapshot.
    assert _read_app(git_repo) == before


def test_rule_id_falls_back_without_finding_ref(git_repo):
    fix = _make_fix()
    del fix["finding_ref"]
    result = fix_applier.apply_fixes(git_repo, [fix], dry_run=True)
    assert result.applied[0]["rule_id"] == "unknown-rule"
    assert result.applied[0]["key"].startswith("app.py:4:")


def test_multiple_fixes_single_commit(git_repo):
    # Add a second vulnerable line to the file and commit it.
    src = VULN_SOURCE + "\nAPI_KEY = 'sk-hardcoded-secret'\n"
    with open(os.path.join(git_repo, "app.py"), "w", encoding="utf-8") as handle:
        handle.write(src)
    _git(git_repo, "commit", "-am", "add secret")

    fix1 = _make_fix()
    fix2 = _make_fix(
        line=6,
        finding_ref="app.py:6:python.lang.security.hardcoded-secret",
        original_code="API_KEY = 'sk-hardcoded-secret'",
        fixed_code="API_KEY = os.environ['API_KEY']",
    )
    result = fix_applier.apply_fixes(git_repo, [fix1, fix2], dry_run=False)

    assert len(result.applied) == 2
    assert result.commit is not None
    # Exactly one new commit for the batch.
    count = _git(git_repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert count == "3"  # initial, add secret, batch fix
    msg = _git(git_repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert "2 automated fixes" in msg


# --------------------------------------------------------------------------
# path-traversal / containment (security regression)
# --------------------------------------------------------------------------

def test_absolute_path_fix_is_skipped_and_writes_nothing(git_repo, tmp_path):
    """A fix whose ``file`` is absolute must never be applied (arbitrary write)."""
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL\n", encoding="utf-8")
    fix = _make_fix(
        file=str(victim),
        original_code="ORIGINAL",
        fixed_code="PWNED",
    )
    result = fix_applier.apply_fixes(git_repo, [fix], dry_run=False)

    assert result.applied == []
    assert len(result.skipped) == 1
    assert "unsafe path" in result.skipped[0]["reason"]
    # The out-of-repo file is untouched.
    assert victim.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_traversal_path_fix_is_skipped_and_writes_nothing(git_repo, tmp_path):
    """A fix whose ``file`` uses ``..`` to escape the repo must be rejected."""
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL\n", encoding="utf-8")
    fix = _make_fix(
        file="../victim.txt",
        original_code="ORIGINAL",
        fixed_code="PWNED",
    )
    result = fix_applier.apply_fixes(git_repo, [fix], dry_run=False)

    assert result.applied == []
    assert len(result.skipped) == 1
    assert "unsafe path" in result.skipped[0]["reason"]
    assert victim.read_text(encoding="utf-8") == "ORIGINAL\n"
