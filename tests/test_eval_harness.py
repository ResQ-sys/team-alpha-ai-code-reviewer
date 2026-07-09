"""Tests for the offline CWE-detection evaluation harness.

All tests are hermetic: semgrep is stubbed via monkeypatch, fixtures live in
``tmp_path``, and reports are written to ``tmp_path`` so the repo is never
mutated. No real subprocess, LLM, or network access occurs.
"""
from __future__ import annotations

import json
import os

import pytest

from evaluation import harness
from evaluation.harness import _prf, _score, normalize_cwe, run_eval


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_fixture(root, name, expected_cwes, source="x = 1\n"):
    """Create a fixture dir with a source file and expected.json manifest."""
    fixture_dir = os.path.join(root, name)
    os.makedirs(fixture_dir)
    with open(os.path.join(fixture_dir, "vuln.py"), "w", encoding="utf-8") as handle:
        handle.write(source)
    with open(os.path.join(fixture_dir, "expected.json"), "w", encoding="utf-8") as handle:
        json.dump({"expected_cwes": expected_cwes}, handle)
    return fixture_dir


def _stub_semgrep(monkeypatch, cwe_by_path):
    """Patch harness.run_semgrep to emit findings keyed by fixture basename."""
    monkeypatch.setattr(harness, "is_semgrep_available", lambda: True)

    def fake_run(repo_path, timeout=120):
        key = os.path.basename(repo_path.rstrip(os.sep))
        cwes = cwe_by_path.get(key, [])
        findings = [{"cwe": [c], "rule_id": f"stub-{c}"} for c in cwes]
        return findings, []

    monkeypatch.setattr(harness, "run_semgrep", fake_run)


# ---------------------------------------------------------------------------
# normalize_cwe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CWE-89", "CWE-89"),
        ("cwe_78", "CWE-78"),
        ("CWE-327: Weak Hash", "CWE-327"),
        ("cwe 502", "CWE-502"),
        (95, "CWE-95"),
        ("no cwe here", None),
        (None, None),
        ("", None),
    ],
)
def test_normalize_cwe(raw, expected):
    assert normalize_cwe(raw) == expected


# ---------------------------------------------------------------------------
# _prf / _score math
# ---------------------------------------------------------------------------

def test_prf_perfect():
    assert _prf(3, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_prf_half():
    # tp=1, fp=1, fn=1 -> P=0.5, R=0.5, F1=0.5
    assert _prf(1, 1, 1) == {"precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_prf_all_zero_is_safe():
    assert _prf(0, 0, 0) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_prf_precision_recall_asymmetric():
    # tp=2, fp=0, fn=2 -> P=1.0, R=0.5, F1=0.6667
    result = _prf(2, 0, 2)
    assert result["precision"] == 1.0
    assert result["recall"] == 0.5
    assert result["f1"] == round(2 * 1.0 * 0.5 / 1.5, 4)


def test_score_partial_match():
    scored = _score({"CWE-89", "CWE-78"}, {"CWE-89", "CWE-327"})
    assert scored["true_positives"] == ["CWE-89"]
    assert scored["false_positives"] == ["CWE-327"]
    assert scored["false_negatives"] == ["CWE-78"]
    assert scored["precision"] == 0.5
    assert scored["recall"] == 0.5
    assert scored["f1"] == 0.5


# ---------------------------------------------------------------------------
# run_eval end-to-end (semgrep stubbed)
# ---------------------------------------------------------------------------

def test_run_eval_perfect_detection(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "sqli", ["CWE-89"])
    _make_fixture(str(fixtures), "cmd", ["CWE-78"])
    _stub_semgrep(monkeypatch, {"sqli": ["CWE-89"], "cmd": ["CWE-78"]})

    report = run_eval(
        str(fixtures), use_pipeline=False, out_dir=str(tmp_path / "out")
    )

    assert report["num_fixtures"] == 2
    agg = report["aggregate"]
    assert (agg["tp"], agg["fp"], agg["fn"]) == (2, 0, 0)
    assert agg["precision"] == 1.0
    assert agg["recall"] == 1.0
    assert agg["f1"] == 1.0
    assert agg["fix_success"] is None  # no pipeline -> no fixes gathered


def test_run_eval_mixed_confusion_math(tmp_path, monkeypatch):
    """One fixture: TP=1 (CWE-89), FP=1 (CWE-327), FN=1 (CWE-78)."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "mixed", ["CWE-89", "CWE-78"])
    _stub_semgrep(monkeypatch, {"mixed": ["CWE-89", "CWE-327"]})

    report = run_eval(
        str(fixtures), use_pipeline=False, out_dir=str(tmp_path / "out")
    )

    item = report["per_fixture"][0]
    assert item["true_positives"] == ["CWE-89"]
    assert item["false_positives"] == ["CWE-327"]
    assert item["false_negatives"] == ["CWE-78"]
    assert item["precision"] == 0.5
    assert item["recall"] == 0.5
    assert item["f1"] == 0.5

    agg = report["aggregate"]
    assert (agg["tp"], agg["fp"], agg["fn"]) == (1, 1, 1)
    assert agg["f1"] == 0.5


def test_run_eval_aggregate_micro_average(tmp_path, monkeypatch):
    """Aggregate is a micro-average over summed TP/FP/FN across fixtures."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "a", ["CWE-89"])           # detected -> TP
    _make_fixture(str(fixtures), "b", ["CWE-78"])           # missed   -> FN
    _stub_semgrep(monkeypatch, {"a": ["CWE-89"], "b": ["CWE-999"]})

    report = run_eval(
        str(fixtures), use_pipeline=False, out_dir=str(tmp_path / "out")
    )
    agg = report["aggregate"]
    # a: tp1; b: fp1(CWE-999), fn1(CWE-78) -> tp=1, fp=1, fn=1
    assert (agg["tp"], agg["fp"], agg["fn"]) == (1, 1, 1)
    assert agg["precision"] == 0.5
    assert agg["recall"] == 0.5


def test_run_eval_semgrep_missing_is_graceful(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "sqli", ["CWE-89"])
    monkeypatch.setattr(harness, "is_semgrep_available", lambda: False)

    report = run_eval(
        str(fixtures), use_pipeline=False, out_dir=str(tmp_path / "out")
    )

    assert report["semgrep_available"] is False
    item = report["per_fixture"][0]
    assert item["detected_cwes"] == []
    assert item["recall"] == 0.0  # nothing detected
    assert any("semgrep unavailable" in n for n in item["notes"])


def test_run_eval_writes_reports(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "sqli", ["CWE-89"])
    _stub_semgrep(monkeypatch, {"sqli": ["CWE-89"]})
    out_dir = tmp_path / "out"

    run_eval(str(fixtures), use_pipeline=False, out_dir=str(out_dir))

    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text())
    assert data["aggregate"]["precision"] == 1.0
    assert "# CWE Detection Evaluation Report" in md_path.read_text()


def test_run_eval_llm_supplements_detection(tmp_path, monkeypatch):
    """An injected LLM can add detections the static pass missed."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "logic", ["CWE-89", "CWE-862"])
    # semgrep only catches CWE-89; LLM supplies the missing CWE-862.
    _stub_semgrep(monkeypatch, {"logic": ["CWE-89"]})

    class FakeLLM:
        def complete_schema(self, system, prompt, schema, **kwargs):
            return {"cwes": ["CWE-862"]}

    report = run_eval(
        str(fixtures),
        use_pipeline=False,
        llm=FakeLLM(),
        out_dir=str(tmp_path / "out"),
    )
    item = report["per_fixture"][0]
    assert item["detected_cwes"] == ["CWE-862", "CWE-89"]
    assert item["false_negatives"] == []
    assert item["recall"] == 1.0


def test_run_eval_pipeline_fix_success(tmp_path, monkeypatch):
    """use_pipeline path derives detections + fix-success from run_pipeline."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _make_fixture(str(fixtures), "sqli", ["CWE-89"])
    # No semgrep; detection comes entirely from the stubbed pipeline.
    monkeypatch.setattr(harness, "is_semgrep_available", lambda: False)

    def fake_pipeline(repo_path, auto_approve=True):
        return {
            "final_report_json": {
                "bug_and_vulnerability_findings": [{"cwe": ["CWE-89"]}],
                "suggested_fixes": [
                    {"fixed_code": "safe()", "verified": True},
                    {"fixed_code": "maybe()", "verified": False},
                ],
                "notices": [],
            }
        }

    import graph
    monkeypatch.setattr(graph, "run_pipeline", fake_pipeline)

    report = run_eval(
        str(fixtures), use_pipeline=True, out_dir=str(tmp_path / "out")
    )
    item = report["per_fixture"][0]
    assert item["detected_cwes"] == ["CWE-89"]
    assert item["recall"] == 1.0
    # 1 of 2 candidate fixes verified.
    assert report["aggregate"]["fix_success"] == 0.5


def test_run_eval_no_fixtures_dir_is_empty(tmp_path):
    report = run_eval(
        str(tmp_path / "does_not_exist"),
        use_pipeline=False,
        out_dir=str(tmp_path / "out"),
    )
    assert report["num_fixtures"] == 0
    assert report["aggregate"]["f1"] == 0.0


def test_bundled_fixtures_have_valid_manifests():
    """Every packaged fixture parses and covers the required CWE spectrum."""
    from evaluation.harness import _discover_fixtures, _load_expected

    fixtures_dir = os.path.join(os.path.dirname(harness.__file__), "fixtures")
    dirs = _discover_fixtures(fixtures_dir)
    assert len(dirs) >= 3
    all_cwes = set()
    for fixture_dir in dirs:
        all_cwes |= _load_expected(fixture_dir)
    required = {"CWE-89", "CWE-78", "CWE-327", "CWE-502", "CWE-95", "CWE-798"}
    assert required.issubset(all_cwes)
