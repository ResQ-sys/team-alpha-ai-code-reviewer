"""Diff generation and indentation-tolerant code replacement.

LLM-suggested fixes rarely reproduce the original snippet byte-for-byte:
indentation, trailing whitespace, or internal spacing drift. This module
locates the target snippet with an exact match first, then falls back to a
whitespace-normalized, indentation-tolerant match, and re-indents the
replacement to the block it displaces.

Public contract (consumed by ``secure_dev.fix_applier`` /
``secure_dev.repair_loop``)::

    @dataclass AppliedPatch(file, line, diff, applied, error=None)
    make_unified_diff(rel_path, original, fixed) -> str
    locate_and_replace(file_text, original_code, fixed_code) -> tuple[str, bool]
    apply_fix_to_file(repo, rel_file, original_code, fixed_code) -> AppliedPatch
"""

from __future__ import annotations

import difflib
import os
import textwrap
from dataclasses import dataclass
from typing import Optional

from secure_dev.safe_path import PathEscapeError, resolve_within


@dataclass
class AppliedPatch:
    """Outcome of applying a single fix to one file."""

    file: str
    line: int
    diff: str
    applied: bool
    error: Optional[str] = None


def make_unified_diff(rel_path: str, original: str, fixed: str) -> str:
    """Return a unified diff transforming *original* into *fixed*.

    Uses ``a/<rel_path>`` / ``b/<rel_path>`` headers so the output is directly
    readable as a git-style patch. Returns an empty string when the texts are
    identical.
    """
    if original == fixed:
        return ""
    original_lines = original.splitlines(keepends=True)
    fixed_lines = fixed.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        fixed_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="\n",
    )
    return "".join(diff)


def _normalize_line(line: str) -> str:
    """Collapse a line to indentation- and whitespace-insensitive form."""
    return " ".join(line.split())


def _reindent(replacement: str, base_indent: str) -> str:
    """Re-base *replacement* onto *base_indent*.

    The replacement is dedented to its own minimal indentation, then each
    non-blank line is prefixed with *base_indent* (the indentation of the block
    being replaced). Blank lines stay blank to avoid trailing-whitespace noise.
    """
    dedented = textwrap.dedent(replacement)
    out_lines = []
    for line in dedented.splitlines():
        if line.strip() == "":
            out_lines.append("")
        else:
            out_lines.append(base_indent + line)
    return "\n".join(out_lines)


def _fuzzy_replace(
    file_text: str, original_code: str, fixed_code: str
) -> tuple[str, bool]:
    """Whitespace-normalized, indentation-tolerant block replacement.

    Matches a contiguous run of file lines whose normalized forms equal the
    normalized lines of *original_code*, then substitutes *fixed_code*
    re-indented to the matched block. Returns ``(text, replaced?)``.
    """
    orig_lines = original_code.splitlines()
    # Drop leading/trailing blank lines from the needle so surrounding blank
    # lines in the suggestion do not defeat the match.
    while orig_lines and orig_lines[0].strip() == "":
        orig_lines.pop(0)
    while orig_lines and orig_lines[-1].strip() == "":
        orig_lines.pop()
    if not orig_lines:
        return file_text, False

    needle = [_normalize_line(line) for line in orig_lines]
    file_lines = file_text.splitlines(keepends=True)
    stripped = [_normalize_line(line) for line in file_lines]

    span = len(needle)
    for start in range(0, len(file_lines) - span + 1):
        if stripped[start : start + span] != needle:
            continue

        base_indent = file_lines[start][
            : len(file_lines[start]) - len(file_lines[start].lstrip())
        ]
        # Preserve whether the final replaced line carried a newline so we do
        # not accidentally merge into or split from the following line.
        last = file_lines[start + span - 1]
        trailing_newline = last.endswith("\n")

        replacement = _reindent(fixed_code, base_indent)
        if trailing_newline:
            replacement += "\n"

        new_lines = file_lines[:start] + [replacement] + file_lines[start + span :]
        return "".join(new_lines), True

    return file_text, False


def locate_and_replace(
    file_text: str, original_code: str, fixed_code: str
) -> tuple[str, bool]:
    """Replace *original_code* with *fixed_code* inside *file_text*.

    Tries an exact substring match first (fast, unambiguous), then falls back
    to an indentation-tolerant, whitespace-normalized block match. Only the
    first occurrence is replaced. Returns ``(new_text, replaced?)``.
    """
    if not original_code:
        return file_text, False

    if original_code in file_text:
        return file_text.replace(original_code, fixed_code, 1), True

    return _fuzzy_replace(file_text, original_code, fixed_code)


def _match_line(file_text: str, original_code: str) -> int:
    """Return the 1-based line where *original_code* begins, or 0 if unknown."""
    if original_code and original_code in file_text:
        return file_text[: file_text.index(original_code)].count("\n") + 1

    orig_lines = [ln for ln in original_code.splitlines() if ln.strip()]
    if not orig_lines:
        return 0
    needle = [_normalize_line(ln) for ln in orig_lines]
    file_lines = file_text.splitlines()
    stripped = [_normalize_line(ln) for ln in file_lines]
    span = len(needle)
    for start in range(0, len(file_lines) - span + 1):
        if stripped[start : start + span] == needle:
            return start + 1
    return 0


def apply_fix_to_file(
    repo: str, rel_file: str, original_code: str, fixed_code: str
) -> AppliedPatch:
    """Read *rel_file*, replace the snippet, write it back, return an AppliedPatch.

    The returned patch always carries a real unified diff of the change (empty
    when nothing changed). The file is only written when a replacement actually
    occurs, so a missed match leaves the file untouched.
    """
    try:
        abs_path = resolve_within(repo, rel_file)
    except PathEscapeError as exc:
        return AppliedPatch(
            file=rel_file, line=0, diff="", applied=False,
            error=f"unsafe path rejected: {exc}",
        )

    if not os.path.isfile(abs_path):
        return AppliedPatch(
            file=rel_file, line=0, diff="", applied=False,
            error=f"file not found: {abs_path}",
        )

    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            original_text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return AppliedPatch(
            file=rel_file, line=0, diff="", applied=False,
            error=f"could not read file: {exc}",
        )

    line = _match_line(original_text, original_code)
    new_text, replaced = locate_and_replace(original_text, original_code, fixed_code)

    if not replaced:
        return AppliedPatch(
            file=rel_file, line=line, diff="", applied=False,
            error="original_code not found in file",
        )

    diff = make_unified_diff(rel_file, original_text, new_text)

    if new_text == original_text:
        # Match succeeded but the fix is a no-op; nothing to write.
        return AppliedPatch(
            file=rel_file, line=line, diff="", applied=False,
            error="fix produced no change",
        )

    try:
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(new_text)
    except OSError as exc:
        return AppliedPatch(
            file=rel_file, line=line, diff=diff, applied=False,
            error=f"could not write file: {exc}",
        )

    return AppliedPatch(file=rel_file, line=line, diff=diff, applied=True, error=None)
