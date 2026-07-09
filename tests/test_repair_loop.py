"""Unit tests for ``secure_dev.repair_loop``.

Every test operates on a ``tmp_path`` repo copy, mocks the LLM, and
monkeypatches ``run_semgrep`` — no real scan, LLM call, or network access
occurs. The original repo is never mutated (the loop works on a temp copy).

Covered scenarios:
  * first attempt resolves,
  * two attempts needed (LLM improves the fix),
  * max attempts exhausted (never resolves),
  * plus guards: no target file, no original_code, pytest failure gating,
    and the original tree staying untouched.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secure_dev import repair_loop  # noqa: E402


# --------------------------------------------------------------------------
# Helpers / fakes
# --------------------------------------------------------------------------

class FakeLLM:
    """Mock LLMClient.complete_schema that returns queued fixed_code values."""

    def __init__(self, fixes):
        self._fixes = list(fixes)
        self.calls = 0

    def complete_schema(self, system, prompt, schema, max_tokens=1500, retries=2):
        self.calls += 1
        if not self._fixes:
            return {"_parse_error": True, "_raw": ""}
        nxt = self._fixes.pop(0)
        if nxt is None:
            return {"_parse_error": True, "_raw": "garbage"}
        return {"fixed_code": nxt, "explanation": "improved"}


def _make_repo(tmp_path, rel="app.py", content="q = 'SELECT ' + user\n"):
    """Create a minimal repo with one source file. Returns (repo, rel)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(repo), rel


def _semgrep_sequence(*fires_flags, rule_id="sqli"):
    """Build a run_semgrep stub whose Nth call fires the rule per *fires_flags*.

    Each flag is a bool: True => the rule fires on that call, False => clean.
    After the sequence is exhausted the last flag repeats.
    """
    state = {"i": 0}
    flags = list(fires_flags)

    def _fake_run_semgrep(path, timeout=300):
        idx = min(state["i"], len(flags) - 1)
        fires = flags[idx]
        state["i"] += 1
        findings = [{"rule_id": rule_id, "file": path}] if fires else []
        return findings, []

    return _fake_run_semgrep


# --------------------------------------------------------------------------
# Scenario (a): first attempt resolves
# --------------------------------------------------------------------------

def test_first_attempt_resolves(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    # After applying the fix the rule no longer fires (clean on first scan).
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(False))
    llm = FakeLLM([])  # should never be called

    finding = {"file": rel, "rule_id": "sqli", "cwe": ["CWE-89"]}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = db.execute('SELECT ?', (user,))",
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, run_tests=False, llm=llm
    )

    assert result.resolved is True
    assert result.regressed is False
    assert result.attempts == 1
    assert result.fixed_code == fix["fixed_code"]
    assert llm.calls == 0
    # Original repo file untouched (work happened on a temp copy).
    assert (tmp_path / "repo" / rel).read_text() == "q = 'SELECT ' + user\n"


# --------------------------------------------------------------------------
# Scenario (b): needs two attempts, LLM improves the fix
# --------------------------------------------------------------------------

def test_two_attempts_needed(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    # First scan: still fires. Second scan (after improved fix): clean.
    monkeypatch.setattr(
        repair_loop, "run_semgrep", _semgrep_sequence(True, False)
    )
    improved = "q = db.execute('SELECT ?', (user,))"
    llm = FakeLLM([improved])

    finding = {"file": rel, "rule_id": "sqli", "cwe": ["CWE-89"]}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = 'SELECT ' + escape(user)",  # weak first attempt
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, run_tests=False, llm=llm
    )

    assert result.resolved is True
    assert result.attempts == 2
    assert result.fixed_code == improved
    assert llm.calls == 1


# --------------------------------------------------------------------------
# Scenario (c): exhausts max_attempts, never resolves
# --------------------------------------------------------------------------

def test_exhausts_max_attempts(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    # The rule fires on every scan no matter what we apply.
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(True))
    llm = FakeLLM(["fix-v2", "fix-v3", "fix-v4"])

    finding = {"file": rel, "rule_id": "sqli", "cwe": ["CWE-89"]}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = 'SELECT ' + str(user)",
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, max_attempts=3, run_tests=False, llm=llm
    )

    assert result.resolved is False
    assert result.regressed is True
    assert result.attempts == 3
    # LLM asked for an improvement after attempts 1 and 2 (not after the last).
    assert llm.calls == 2
    assert len(result.notes) >= 3


# --------------------------------------------------------------------------
# LLM stops improving -> loop halts early
# --------------------------------------------------------------------------

def test_stops_when_llm_cannot_improve(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(True))
    llm = FakeLLM([None])  # LLM returns a parse error on first ask

    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = 'SELECT ' + str(user)",
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, max_attempts=3, run_tests=False, llm=llm
    )

    assert result.resolved is False
    assert result.attempts == 1  # gave up after the first rejected attempt
    assert any("no improved fix" in n for n in result.notes)


# --------------------------------------------------------------------------
# pytest failure gates a fix that otherwise cleared the finding
# --------------------------------------------------------------------------

def test_test_failure_blocks_resolution(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    # Finding is cleared on both scans, but tests fail both times.
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(False))
    monkeypatch.setattr(repair_loop, "_run_pytest", lambda repo_dir: False)
    llm = FakeLLM(["improved-but-still-breaks-tests"])

    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = safe(user)",
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, max_attempts=2, run_tests=True, llm=llm
    )

    assert result.resolved is False
    assert result.test_ok is False
    assert result.attempts == 2


def test_test_pass_allows_resolution(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(False))
    monkeypatch.setattr(repair_loop, "_run_pytest", lambda repo_dir: True)
    llm = FakeLLM([])

    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = safe(user)",
    }

    result = repair_loop.repair_finding(
        repo, finding, fix, run_tests=True, llm=llm
    )

    assert result.resolved is True
    assert result.test_ok is True


# --------------------------------------------------------------------------
# Guard rails
# --------------------------------------------------------------------------

def test_missing_target_file_is_handled(tmp_path):
    repo, _rel = _make_repo(tmp_path)
    finding = {"rule_id": "sqli"}  # no file
    fix = {"original_code": "x", "fixed_code": "y"}  # no file
    result = repair_loop.repair_finding(repo, finding, fix, llm=FakeLLM([]))
    assert result.resolved is False
    assert result.attempts == 0
    assert any("no target file" in n for n in result.notes)


def test_missing_original_code_is_handled(tmp_path):
    repo, rel = _make_repo(tmp_path)
    finding = {"file": rel, "rule_id": "sqli"}
    fix = {"file": rel, "fixed_code": "y"}  # no original_code
    result = repair_loop.repair_finding(repo, finding, fix, llm=FakeLLM([]))
    assert result.resolved is False
    assert result.attempts == 0
    assert any("no original_code" in n for n in result.notes)


def test_unapplicable_fix_reported_as_regression(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    # run_semgrep should not even be reached when the snippet cannot be located,
    # but stub it defensively so a real scan never runs.
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(True))
    llm = FakeLLM([])  # nothing better to offer
    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "this snippet is not in the file",
        "fixed_code": "irrelevant",
    }
    result = repair_loop.repair_finding(
        repo, finding, fix, max_attempts=2, run_tests=False, llm=llm
    )
    assert result.resolved is False
    assert result.regressed is True
    assert any("could not apply fix" in n for n in result.notes)


def test_repo_path_not_a_directory(tmp_path):
    missing = str(tmp_path / "does_not_exist")
    finding = {"file": "app.py", "rule_id": "sqli"}
    fix = {"file": "app.py", "original_code": "x", "fixed_code": "y"}
    result = repair_loop.repair_finding(missing, finding, fix, llm=FakeLLM([]))
    assert result.resolved is False
    assert result.attempts == 0
    assert any("not a directory" in n for n in result.notes)


def test_llm_non_string_fix_is_rejected(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(True))

    class WeirdLLM:
        calls = 0

        def complete_schema(self, *a, **k):
            WeirdLLM.calls += 1
            return {"fixed_code": 12345}  # not a string -> must be rejected

    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = 'SELECT ' + str(user)",
    }
    result = repair_loop.repair_finding(
        repo, finding, fix, max_attempts=3, run_tests=False, llm=WeirdLLM()
    )
    assert result.resolved is False
    # Loop halts once the LLM cannot supply a usable string fix.
    assert result.attempts == 1


# --------------------------------------------------------------------------
# Real (non-mocked) pytest sandbox helpers
# --------------------------------------------------------------------------

def test_run_pytest_real_pass(tmp_path):
    repo = tmp_path / "pyrepo"
    repo.mkdir()
    (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    assert repair_loop._repo_has_tests(str(repo)) is True
    assert repair_loop._run_pytest(str(repo)) is True


def test_run_pytest_no_tests_is_inconclusive(tmp_path):
    repo = tmp_path / "notests"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n")
    assert repair_loop._repo_has_tests(str(repo)) is False
    assert repair_loop._run_pytest(str(repo)) is None


def test_original_repo_never_mutated(tmp_path, monkeypatch):
    repo, rel = _make_repo(tmp_path)
    monkeypatch.setattr(repair_loop, "run_semgrep", _semgrep_sequence(True))
    llm = FakeLLM(["a", "b", "c"])
    finding = {"file": rel, "rule_id": "sqli"}
    fix = {
        "file": rel,
        "original_code": "q = 'SELECT ' + user",
        "fixed_code": "q = 'changed'",
    }
    repair_loop.repair_finding(
        repo, finding, fix, max_attempts=3, run_tests=False, llm=llm
    )
    # Despite several applied candidates in the sandbox, the real file is intact.
    assert (tmp_path / "repo" / rel).read_text() == "q = 'SELECT ' + user\n"
