"""
Unit tests for the core pipeline logic — complements the end-to-end
tests/test_pipeline.py with focused coverage of the pieces that carry the
most logic and that the review specifically called out:

  - finding categorization (syntax / logic / security / quality)
  - syntax-error promotion from the parser
  - near-duplicate merging of semgrep + LLM findings
  - quality-score behaviour (bounds, clamping, monotonicity)
  - verifier self-checks (empty / no-op / valid)
  - agent tracing / observability spans
  - dataset-backed RAG corpus

Run with:  python -m unittest tests.test_unit   (no API key / LLM needed)
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.report_agent import _compute_quality_score  # noqa: E402
from agents.verifier_agent import verifier_node  # noqa: E402
from agents.vulnerability_agent import (  # noqa: E402
    _merge_near_duplicates,
    classify_finding,
    vulnerability_node,
)
from rag.dataset_loader import load_dataset_docs  # noqa: E402
from utils.code_parser import build_code_graph  # noqa: E402
from utils.tracing import summarize_trace, traced  # noqa: E402


def _f(**kw):
    base = {"file": "a.py", "line": 1, "rule_id": "", "cwe": [], "severity": "WARNING",
            "message": "m", "source": "llm"}
    base.update(kw)
    return base


class TestCategorization(unittest.TestCase):
    def test_classify_each_category(self):
        self.assertEqual(classify_finding(_f(source="parser", rule_id="syntax-error")), "syntax")
        self.assertEqual(classify_finding(_f(rule_id="llm-bug")), "logic")
        self.assertEqual(classify_finding(_f(rule_id="llm-quality")), "quality")
        self.assertEqual(classify_finding(_f(rule_id="llm-vulnerability")), "security")
        self.assertEqual(classify_finding(_f(source="semgrep", rule_id="python.sqli")), "security")

    def test_vulnerability_node_buckets_and_breakdown(self):
        state = {
            "syntax_findings": [_f(source="parser", rule_id="syntax-error", severity="CRITICAL", line=1)],
            "semgrep_findings": [_f(source="semgrep", rule_id="python.sqli", severity="ERROR", line=10)],
            "llm_findings": [
                _f(rule_id="llm-bug", line=20),
                _f(rule_id="llm-quality", line=30),
            ],
        }
        out = vulnerability_node(state)
        cb = out["category_breakdown"]
        self.assertEqual(cb["syntax"], 1)
        self.assertEqual(cb["logic"], 1)
        self.assertEqual(cb["security"], 1)
        self.assertEqual(cb["quality"], 1)
        # quality is separated out; syntax/logic/security are in merged_findings
        self.assertEqual(len(out["quality_issues"]), 1)
        self.assertEqual({f["category"] for f in out["merged_findings"]},
                         {"syntax", "logic", "security"})

    def test_syntax_finding_never_dropped_by_merge(self):
        # a syntax finding sharing a line-bucket with a semgrep hit must survive
        state = {
            "syntax_findings": [_f(source="parser", rule_id="syntax-error", severity="CRITICAL", line=1)],
            "semgrep_findings": [_f(source="semgrep", rule_id="python.sqli", severity="ERROR", line=1)],
            "llm_findings": [],
        }
        out = vulnerability_node(state)
        self.assertTrue(any(f["category"] == "syntax" for f in out["merged_findings"]))


class TestParserSyntax(unittest.TestCase):
    def test_syntax_error_detected(self):
        graph = build_code_graph("bad.py", "def f(:\n    pass\n", "python")
        self.assertIn("syntax_error", graph)
        self.assertGreaterEqual(graph["syntax_error"]["line"], 1)

    def test_valid_python_has_no_syntax_error(self):
        graph = build_code_graph("ok.py", "def f():\n    return 1\n", "python")
        self.assertNotIn("syntax_error", graph)
        self.assertEqual(len(graph["functions"]), 1)


class TestDedup(unittest.TestCase):
    def test_semgrep_and_llm_same_line_merge(self):
        findings = [
            _f(source="semgrep", rule_id="python.sqli", line=10, message="SQLi"),
            _f(source="llm", rule_id="llm-vulnerability", line=10, message="possible injection"),
        ]
        merged = _merge_near_duplicates(findings)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "semgrep")   # semgrep preferred
        self.assertIn("LLM context", merged[0]["message"])  # llm kept as context

    def test_distinct_lines_not_merged(self):
        findings = [
            _f(source="semgrep", rule_id="r1", line=10),
            _f(source="semgrep", rule_id="r2", line=90),
        ]
        self.assertEqual(len(_merge_near_duplicates(findings)), 2)


class TestQualityScore(unittest.TestCase):
    def test_bounds_and_clean(self):
        self.assertEqual(_compute_quality_score([], 100), 100.0)
        many_critical = [{"severity": "CRITICAL"} for _ in range(50)]
        score = _compute_quality_score(many_critical, 50)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)

    def test_monotonic_more_findings_lower_score(self):
        few = _compute_quality_score([{"severity": "ERROR"}], 200)
        many = _compute_quality_score([{"severity": "ERROR"}] * 6, 200)
        self.assertLess(many, few)

    def test_larger_repo_scores_higher_for_same_findings(self):
        findings = [{"severity": "ERROR"}] * 5
        small = _compute_quality_score(findings, 50)
        large = _compute_quality_score(findings, 5000)
        self.assertGreaterEqual(large, small)


class TestVerifier(unittest.TestCase):
    def _run(self, original, fixed):
        state = {
            "suggested_fixes": [{
                "file": "a.py", "line": 1, "finding_ref": "a.py:1:llm-bug",
                "original_code": original, "fixed_code": fixed,
            }],
            "languages": {"a.py": "python"},
        }
        return verifier_node(state)["suggested_fixes"][0]

    def test_empty_fix_flagged(self):
        self.assertFalse(self._run("x = 1", "")["verified"])

    def test_noop_fix_flagged(self):
        self.assertFalse(self._run("x = 1", "x = 1")["verified"])

    def test_valid_fix_passes(self):
        fix = self._run("md5(p)", "from hashlib import scrypt\nscrypt(p, salt=s)")
        self.assertTrue(fix["verified"])

    def test_confidence_score_reflects_flags(self):
        state = {
            "suggested_fixes": [
                {"file": "a.py", "line": 1, "finding_ref": "a.py:1:llm-bug",
                 "original_code": "x=1", "fixed_code": ""},                    # flagged
                {"file": "a.py", "line": 2, "finding_ref": "a.py:2:llm-bug",
                 "original_code": "y=1", "fixed_code": "y = validate(1)"},     # ok
            ],
            "languages": {"a.py": "python"},
        }
        rep = verifier_node(state)["verifier_report"]
        self.assertEqual(rep["total_fixes"], 2)
        self.assertEqual(rep["flagged_fixes"], 1)
        self.assertEqual(rep["confidence_score"], 0.5)


class TestTracing(unittest.TestCase):
    def test_span_recorded(self):
        node = lambda s: {**s, "merged_findings": [1, 2, 3]}
        out = traced("demo", node)({})
        self.assertEqual(len(out["trace"]), 1)
        span = out["trace"][0]
        self.assertEqual(span["node"], "demo")
        self.assertEqual(span["status"], "ok")
        self.assertEqual(span["produced"].get("merged_findings"), 3)
        self.assertIn("duration_ms", span)

    def test_spans_accumulate_and_sequence(self):
        n1 = traced("a", lambda s: {**s})
        n2 = traced("b", lambda s: {**s})
        out = n2(n1({}))
        self.assertEqual([sp["seq"] for sp in out["trace"]], [1, 2])
        self.assertEqual([sp["node"] for sp in out["trace"]], ["a", "b"])

    def test_error_captured(self):
        def boom(s):
            raise ValueError("nope")
        out = traced("boom", boom)({})
        self.assertEqual(out["trace"][0]["status"], "error")
        self.assertIn("ValueError", out["trace"][0]["error"])
        self.assertTrue(any("boom" in e for e in out["errors"]))

    def test_summarize_trace(self):
        trace = [
            {"node": "a", "duration_ms": 5.0, "status": "ok"},
            {"node": "b", "duration_ms": 20.0, "status": "error"},
        ]
        s = summarize_trace(trace)
        self.assertEqual(s["nodes"], 2)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["slowest"]["node"], "b")


class TestDatasetRAG(unittest.TestCase):
    def test_dataset_docs_loaded(self):
        docs = load_dataset_docs()
        self.assertGreaterEqual(len(docs), 10)
        # each doc has the KB schema + a dataset source
        for d in docs:
            self.assertIn("title", d)
            self.assertIn("text", d)
            self.assertIn("tags", d)
            self.assertIn("source", d)
        # datasets named in the problem statement are represented
        sources = {d["source"] for d in docs}
        self.assertTrue({"Devign", "Big-Vul"} & sources)

    def test_cwe_tags_present(self):
        docs = load_dataset_docs()
        all_tags = {t for d in docs for t in d["tags"]}
        self.assertIn("cwe-476", all_tags)
        self.assertTrue(any(t.startswith("dataset:") for t in all_tags))


if __name__ == "__main__":
    unittest.main(verbosity=2)
