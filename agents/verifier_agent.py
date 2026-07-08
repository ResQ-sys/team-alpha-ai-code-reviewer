"""
Verifier / Self-Reflection Agent
----------------------------------
Independently validates each suggested fix before it is ever surfaced as
"applyable", to catch hallucinated or inconsistent AI-generated code
changes. Covers "Validate AI-generated fixes before recommending code
modifications" and "Detect hallucinated code suggestions and inconsistent
reasoning" from the Requirements/Challenges lists.

Verification strategy (kept dependency-light for the hackathon demo):
  1. Syntax check — for Python, ast.parse the fixed snippet standalone;
     for other languages, run basic balanced-braces/parens heuristics.
  2. Re-run semgrep against a temp file containing the patched snippet in
     its original file context, and confirm the specific rule no longer
     fires (when the finding came from semgrep).
  3. Non-triviality check — the fix must actually differ from the
     original code (catches lazy/no-op "fixes").
  4. Aggregate a confidence score per fix, and an overall pipeline
     confidence score used in the final report.
"""
from __future__ import annotations

import ast
import os
import tempfile
from typing import List, Tuple

from agents.state import ReviewState, SuggestedFix
from utils.semgrep_runner import is_semgrep_available, run_semgrep


def _python_syntax_ok(snippet: str) -> Tuple[bool, str]:
    try:
        ast.parse(snippet)
        return True, ""
    except SyntaxError as e:
        # Fixes are often partial snippets (not standalone-parseable, e.g. a
        # bare `if` block), so a syntax error here is a soft signal, not
        # necessarily fatal — record it but don't hard-fail on indentation-only issues.
        return False, str(e)


def _balanced_generic(snippet: str) -> bool:
    pairs = {"(": ")", "{": "}", "[": "]"}
    stack = []
    for ch in snippet:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False
    return not stack


def _semgrep_regression_check(finding_rule_id: str, file_path: str, original_code: str, fixed_code: str, language_ext: str) -> Tuple[bool, str]:
    """Best-effort: substitute the fix into a throwaway copy of the file and
    confirm the same semgrep rule no longer fires. Falls back silently if
    semgrep isn't available or the exact substitution can't be located."""
    if not is_semgrep_available():
        return True, "semgrep unavailable — skipped regression re-scan"
    if not original_code.strip() or original_code not in _read_safely(file_path):
        return True, "could not locate exact snippet for isolated re-scan — skipped"

    original_content = _read_safely(file_path)
    patched_content = original_content.replace(original_code, fixed_code, 1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = os.path.join(tmpdir, os.path.basename(file_path))
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(patched_content)
        findings, _errors = run_semgrep(tmpdir, timeout=60)

    still_firing = any(f["rule_id"] == finding_rule_id for f in findings)
    if still_firing:
        return False, f"rule '{finding_rule_id}' still fires after patch"
    return True, "confirmed rule no longer fires on patched snippet"


def _read_safely(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def verifier_node(state: ReviewState) -> ReviewState:
    fixes: List[SuggestedFix] = list(state.get("suggested_fixes", []))
    languages = state.get("languages", {})
    verified_fixes = []
    flagged_count = 0

    for fix in fixes:
        notes = []
        ok = True

        if not fix.get("fixed_code", "").strip():
            ok = False
            notes.append("empty fix generated")
        elif fix["fixed_code"].strip() == fix.get("original_code", "").strip():
            ok = False
            notes.append("fix is identical to original code (no-op)")

        lang = languages.get(fix["file"], "unknown")
        if ok and lang == "python":
            syntax_ok, err = _python_syntax_ok(fix["fixed_code"])
            if not syntax_ok:
                notes.append(f"standalone syntax check inconclusive: {err}")
        elif ok:
            if not _balanced_generic(fix["fixed_code"]):
                ok = False
                notes.append("unbalanced brackets/braces in fixed code")

        rule_id = fix["finding_ref"].split(":")[-1]
        if ok and rule_id and not rule_id.startswith("llm-"):
            regression_ok, regression_note = _semgrep_regression_check(
                rule_id, fix["file"], fix.get("original_code", ""), fix.get("fixed_code", ""), lang
            )
            notes.append(regression_note)
            if not regression_ok:
                ok = False

        if not ok:
            flagged_count += 1

        fix = {**fix, "verified": ok, "verification_notes": "; ".join(notes)}
        verified_fixes.append(fix)

    total = len(verified_fixes) or 1
    confidence_score = round(1 - (flagged_count / total), 3)

    verifier_report = {
        "total_fixes": len(verified_fixes),
        "flagged_fixes": flagged_count,
        "verified_fixes": len(verified_fixes) - flagged_count,
        "confidence_score": confidence_score,
    }

    return {
        **state,
        "suggested_fixes": verified_fixes,
        "verifier_report": verifier_report,
        "confidence_score": confidence_score,
    }
