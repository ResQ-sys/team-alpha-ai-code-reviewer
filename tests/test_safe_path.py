"""Unit tests for ``secure_dev.safe_path.resolve_within``.

These pin the containment guard that every read/write in the fix-application
layer relies on. All paths are under ``tmp_path``; nothing touches the project
repo, the network, or any LLM.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secure_dev.safe_path import PathEscapeError, is_within, resolve_within  # noqa: E402


def test_allows_simple_relative_file(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    resolved = resolve_within(str(tmp_path), "app.py")
    assert resolved == os.path.realpath(str(tmp_path / "app.py"))


def test_allows_nested_relative_file(tmp_path):
    resolved = resolve_within(str(tmp_path), "pkg/mod.py")
    assert resolved == os.path.realpath(str(tmp_path / "pkg" / "mod.py"))


def test_rejects_absolute_path(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(str(tmp_path), "/etc/passwd")


def test_rejects_parent_traversal(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(str(tmp_path), "../outside.txt")


def test_rejects_embedded_traversal_that_escapes(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(str(tmp_path), "sub/../../outside.txt")


def test_rejects_symlink_escaping_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("SECRET\n", encoding="utf-8")
    # A symlink inside the repo pointing at a file outside it.
    link = repo / "link.txt"
    os.symlink(str(secret), str(link))
    with pytest.raises(PathEscapeError):
        resolve_within(str(repo), "link.txt")


def test_rejects_empty_inputs(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(str(tmp_path), "")
    with pytest.raises(PathEscapeError):
        resolve_within("", "app.py")


def test_is_within_predicate(tmp_path):
    assert is_within(str(tmp_path), "ok/file.py") is True
    assert is_within(str(tmp_path), "../nope") is False
