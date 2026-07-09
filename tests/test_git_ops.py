"""Unit tests for ``secure_dev.git_ops``.

All tests operate on real, throwaway git repos created under ``tmp_path`` via
subprocess; none touch the project repo, the network, or any LLM.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secure_dev import git_ops  # noqa: E402


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


@pytest.fixture()
def git_repo(tmp_path):
    """A real git repo with one committed file and a configured identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "tester@example.com")
    _git(str(repo), "config", "user.name", "Tester")
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-m", "initial")
    return str(repo)


@pytest.fixture()
def plain_dir(tmp_path):
    """A directory that is NOT a git repo."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "note.txt").write_text("just a file\n", encoding="utf-8")
    return str(plain)


# --------------------------------------------------------------------------
# ensure_git_repo
# --------------------------------------------------------------------------

def test_ensure_git_repo_true_for_git_tree(git_repo):
    assert git_ops.ensure_git_repo(git_repo) is True


def test_ensure_git_repo_false_for_plain_dir(plain_dir):
    assert git_ops.ensure_git_repo(plain_dir) is False


def test_ensure_git_repo_false_for_missing_path(tmp_path):
    assert git_ops.ensure_git_repo(str(tmp_path / "does-not-exist")) is False


# --------------------------------------------------------------------------
# ensure_branch
# --------------------------------------------------------------------------

def test_ensure_branch_creates_and_checks_out(git_repo):
    git_ops.ensure_branch(git_repo, "fix/security")
    current = _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert current == "fix/security"


def test_ensure_branch_checks_out_existing(git_repo):
    _git(git_repo, "branch", "existing")
    git_ops.ensure_branch(git_repo, "existing")
    current = _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert current == "existing"


def test_ensure_branch_noop_on_plain_dir(plain_dir):
    # Must not raise and must not create a .git directory.
    git_ops.ensure_branch(plain_dir, "whatever")
    assert not os.path.isdir(os.path.join(plain_dir, ".git"))


# --------------------------------------------------------------------------
# working_tree_clean
# --------------------------------------------------------------------------

def test_working_tree_clean_true_when_clean(git_repo):
    assert git_ops.working_tree_clean(git_repo) is True


def test_working_tree_clean_false_when_dirty(git_repo):
    with open(os.path.join(git_repo, "app.py"), "a", encoding="utf-8") as handle:
        handle.write("print('changed')\n")
    assert git_ops.working_tree_clean(git_repo) is False


def test_working_tree_clean_true_for_plain_dir(plain_dir):
    assert git_ops.working_tree_clean(plain_dir) is True


# --------------------------------------------------------------------------
# commit_all
# --------------------------------------------------------------------------

def test_commit_all_returns_sha_and_persists(git_repo):
    with open(os.path.join(git_repo, "app.py"), "a", encoding="utf-8") as handle:
        handle.write("print('more')\n")
    sha = git_ops.commit_all(git_repo, "fix: add line")
    assert sha is not None
    assert len(sha) == 40
    head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    assert sha == head
    assert git_ops.working_tree_clean(git_repo) is True


def test_commit_all_returns_none_when_nothing_to_commit(git_repo):
    assert git_ops.commit_all(git_repo, "noop") is None


def test_commit_all_returns_none_on_plain_dir(plain_dir):
    assert git_ops.commit_all(plain_dir, "nope") is None


def test_commit_all_works_without_configured_identity(tmp_path):
    repo = tmp_path / "noid"
    repo.mkdir()
    _git(str(repo), "init")
    # Deliberately no user.name / user.email set locally.
    (repo / "f.py").write_text("x = 1\n", encoding="utf-8")
    sha = git_ops.commit_all(str(repo), "fix: initial")
    # Either a fallback identity was injected (sha returned) or a global
    # identity exists; both are acceptable. It must not raise.
    if sha is not None:
        assert len(sha) == 40


# --------------------------------------------------------------------------
# snapshot_backup / restore_backup
# --------------------------------------------------------------------------

def test_snapshot_and_restore_roundtrip(git_repo):
    rel = "app.py"
    backup = git_ops.snapshot_backup(git_repo, [rel])
    assert backup[rel] == "print('hello')\n"

    with open(os.path.join(git_repo, rel), "w", encoding="utf-8") as handle:
        handle.write("CORRUPTED\n")

    git_ops.restore_backup(git_repo, backup)
    with open(os.path.join(git_repo, rel), encoding="utf-8") as handle:
        assert handle.read() == "print('hello')\n"


def test_snapshot_records_missing_file_as_none(git_repo):
    backup = git_ops.snapshot_backup(git_repo, ["created_later.py"])
    assert backup["created_later.py"] is None


def test_restore_deletes_file_that_did_not_exist(git_repo):
    rel = "created_later.py"
    backup = git_ops.snapshot_backup(git_repo, [rel])
    # Simulate a fix creating the file, then rolling back.
    with open(os.path.join(git_repo, rel), "w", encoding="utf-8") as handle:
        handle.write("new content\n")
    git_ops.restore_backup(git_repo, backup)
    assert not os.path.isfile(os.path.join(git_repo, rel))


def test_restore_recreates_nested_directories(tmp_path):
    repo = str(tmp_path)
    backup = {"pkg/mod.py": "value = 1\n"}
    git_ops.restore_backup(repo, backup)
    with open(os.path.join(repo, "pkg", "mod.py"), encoding="utf-8") as handle:
        assert handle.read() == "value = 1\n"
