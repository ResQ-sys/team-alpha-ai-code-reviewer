"""
Integration test that runs the full LangGraph pipeline end-to-end with the
Code-LLM calls mocked out, so it can run in CI / without an API key. This
validates node wiring, state passing, merge/dedupe logic, verifier logic,
approval routing, and report generation all work together correctly.

Run with: python -m tests.test_pipeline
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the submodule so unittest.mock.patch can resolve the dotted target
# (Python 3.14's patch getter no longer auto-imports intermediate submodules).
import utils.llm_client  # noqa: E402,F401

MOCK_FILE_REVIEW = {
    "summary": "Sample module with intentional demo vulnerabilities.",
    "findings": [
        {
            "line": 40,
            "severity": "WARNING",
            "category": "quality",
            "cwe": [],
            "message": "get_discount compares is_vip to the string 'true' instead of a boolean, silently disabling the VIP discount.",
            "confidence": 0.8,
        }
    ],
}

MOCK_FIX_RESPONSE = {
    "explanation": "This pattern is unsafe because untrusted input reaches a sensitive sink without validation or parameterization.",
    "fixed_code": "# fixed_code_placeholder\npass",
    "confidence": 0.75,
}


def _fake_complete_json(self, system, prompt, max_tokens=1500):
    if "Return JSON with this exact shape" in prompt and "summary" in prompt:
        return MOCK_FILE_REVIEW
    return MOCK_FIX_RESPONSE


def main():
    with patch("utils.llm_client.LLMClient.complete_json", _fake_complete_json):
        from graph import build_graph

        app = build_graph()
        state = {"repo_path": "sample_repo", "auto_approve": True, "errors": []}
        result = app.invoke(state)

    assert result["files"], "ingestion found no files"
    assert result["semgrep_findings"], "expected semgrep findings on the intentionally vulnerable sample repo"
    assert result["merged_findings"], "expected merged findings"
    assert result["suggested_fixes"], "expected suggested fixes"
    assert "confidence_score" in result
    assert result["final_report_md"]
    assert result["final_report_json"]

    print("Quality score:", result["quality_score"])
    print("Confidence score:", result["confidence_score"])
    print("Findings:", len(result["merged_findings"]))
    print("Suggested fixes:", len(result["suggested_fixes"]))
    print("Verifier report:", json.dumps(result["verifier_report"], indent=2))
    print("Pending approval:", len(result["requires_approval"]))
    print("Approved:", len(result["approved_fixes"]))
    print("\nAll pipeline integration assertions passed.")


if __name__ == "__main__":
    main()
