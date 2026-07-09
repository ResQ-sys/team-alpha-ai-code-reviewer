"""Unit tests for ``secure_dev.patch``.

Covers exact and fuzzy (indentation-tolerant) matching, diff generation, and
end-to-end file application. Uses only ``tmp_path``; no network or LLM.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secure_dev import patch  # noqa: E402
from secure_dev.patch import AppliedPatch  # noqa: E402


# --------------------------------------------------------------------------
# make_unified_diff
# --------------------------------------------------------------------------

def test_make_unified_diff_empty_when_identical():
    assert patch.make_unified_diff("a.py", "x = 1\n", "x = 1\n") == ""


def test_make_unified_diff_has_headers_and_change():
    diff = patch.make_unified_diff("a.py", "x = 1\n", "x = 2\n")
    assert "--- a/a.py" in diff
    assert "+++ b/a.py" in diff
    assert "-x = 1" in diff
    assert "+x = 2" in diff


# --------------------------------------------------------------------------
# locate_and_replace: exact
# --------------------------------------------------------------------------

def test_locate_and_replace_exact_match():
    text = "def f():\n    return 1\n"
    new, replaced = patch.locate_and_replace(text, "return 1", "return 2")
    assert replaced is True
    assert "return 2" in new
    assert "return 1" not in new


def test_locate_and_replace_only_first_occurrence():
    text = "a = 1\na = 1\n"
    new, replaced = patch.locate_and_replace(text, "a = 1", "a = 2")
    assert replaced is True
    assert new == "a = 2\na = 1\n"


def test_locate_and_replace_returns_false_when_absent():
    text = "a = 1\n"
    new, replaced = patch.locate_and_replace(text, "nonexistent", "z = 9")
    assert replaced is False
    assert new == text


def test_locate_and_replace_empty_original_is_noop():
    text = "a = 1\n"
    new, replaced = patch.locate_and_replace(text, "", "z = 9")
    assert replaced is False
    assert new == text


# --------------------------------------------------------------------------
# locate_and_replace: fuzzy / indentation-tolerant
# --------------------------------------------------------------------------

def test_fuzzy_match_tolerates_indentation_difference():
    # File line carries 8-space indent; the suggestion snippet uses none.
    text = "class C:\n    def m(self):\n        x = compute()\n"
    original = "x = compute()"  # zero indentation in the suggestion
    new, replaced = patch.locate_and_replace(text, original, "x = compute_safe()")
    assert replaced is True
    # Replacement is re-indented to the matched block's actual 8-space indent.
    assert "        x = compute_safe()\n" in new


def test_fuzzy_match_tolerates_internal_whitespace():
    # Extra whitespace *between* tokens is collapsed for matching.
    text = "y   =   foo(a, b)\n"
    original = "y = foo(a, b)"
    new, replaced = patch.locate_and_replace(text, original, "y = bar(a, b)")
    assert replaced is True
    assert "y = bar(a, b)" in new


def test_fuzzy_multiline_block_reindented():
    text = (
        "def handler():\n"
        "    if user:\n"
        "        run(cmd)\n"
        "        log(cmd)\n"
    )
    original = "run(cmd)\nlog(cmd)"
    fixed = "run(sanitize(cmd))\nlog(cmd)"
    new, replaced = patch.locate_and_replace(text, original, fixed)
    assert replaced is True
    assert "        run(sanitize(cmd))\n" in new
    assert "        log(cmd)\n" in new


def test_fuzzy_preserves_trailing_content():
    text = "start\n    target line\nend\n"
    new, replaced = patch.locate_and_replace(text, "target line", "fixed line")
    assert replaced is True
    assert new == "start\n    fixed line\nend\n"


# --------------------------------------------------------------------------
# apply_fix_to_file
# --------------------------------------------------------------------------

def _write(tmp_path, rel, content):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_apply_fix_to_file_success(tmp_path):
    _write(tmp_path, "src/app.py", "def f():\n    return 1\n")
    result = patch.apply_fix_to_file(str(tmp_path), "src/app.py", "return 1", "return 2")
    assert isinstance(result, AppliedPatch)
    assert result.applied is True
    assert result.error is None
    assert result.line == 2
    assert "-    return 1" in result.diff
    assert "+    return 2" in result.diff
    assert (tmp_path / "src/app.py").read_text() == "def f():\n    return 2\n"


def test_apply_fix_to_file_missing_file(tmp_path):
    result = patch.apply_fix_to_file(str(tmp_path), "nope.py", "a", "b")
    assert result.applied is False
    assert "not found" in (result.error or "")


def test_apply_fix_to_file_snippet_not_found(tmp_path):
    _write(tmp_path, "app.py", "x = 1\n")
    result = patch.apply_fix_to_file(str(tmp_path), "app.py", "does not exist", "y = 2")
    assert result.applied is False
    assert "not found" in (result.error or "")
    # File must be untouched.
    assert (tmp_path / "app.py").read_text() == "x = 1\n"


def test_apply_fix_to_file_reports_line_for_fuzzy_match(tmp_path):
    _write(tmp_path, "app.py", "a = 1\n\n        z = weak()\n")
    result = patch.apply_fix_to_file(str(tmp_path), "app.py", "z = weak()", "z = strong()")
    assert result.applied is True
    assert result.line == 3
    assert "z = strong()" in (tmp_path / "app.py").read_text()


def test_apply_fix_noop_change_not_written(tmp_path):
    _write(tmp_path, "app.py", "a = 1\n")
    result = patch.apply_fix_to_file(str(tmp_path), "app.py", "a = 1", "a = 1")
    assert result.applied is False
    assert "no change" in (result.error or "")
