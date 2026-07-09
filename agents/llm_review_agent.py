"""
Code Understanding Agent (Code LLM)
------------------------------------
Uses a Code-LLM (Code Llama / DeepSeek-Coder / StarCoder / Qwen2.5-Coder in
production, pluggable via utils.llm_client) to:
  1. Summarize each file's purpose/behavior.
  2. Surface *semantic* bugs and vulnerabilities that pattern-based static
     analysis (semgrep) cannot catch — logic errors, race conditions,
     missing auth checks, insecure design decisions — plus code-quality /
     maintainability issues.

This directly covers "Detect bugs, security vulnerabilities, and
inefficient coding patterns using Deep Learning" from the Objective list.
"""
from __future__ import annotations

import json
from typing import Dict, List

from agents.state import FileFinding, ReviewState
from utils.llm_client import LLMClient

SYSTEM_PROMPT = """You are a senior application security engineer and code \
reviewer performing a semantic review that complements static-analysis tools \
like semgrep. Focus on issues static pattern matching would miss: business \
logic flaws, missing authorization/authentication checks, race conditions, \
improper error handling, resource leaks, and maintainability problems. \
Be precise and avoid speculative findings you are not confident about — if \
unsure, lower your confidence rather than inventing an issue."""

FINDING_SCHEMA_HINT = """
Return JSON with this exact shape:
{
  "summary": "<1-3 sentence file purpose summary>",
  "findings": [
    {
      "line": <int, best-effort line number>,
      "severity": "INFO" | "WARNING" | "ERROR" | "CRITICAL",
      "category": "bug" | "vulnerability" | "quality",
      "cwe": ["CWE-XXX", ...],
      "message": "<concise finding description>",
      "confidence": <float 0-1>
    }
  ]
}
"""


def _review_file(client: LLMClient, path: str, content: str, graph_info: Dict) -> Dict:
    truncated = content[:12000]  # guard context length for very large files
    prompt = f"""File: {path}

Code graph context (functions/classes/flagged dangerous calls detected by static parsing):
{json.dumps(graph_info, indent=2)[:2000]}

Source code:
```
{truncated}
```
{FINDING_SCHEMA_HINT}
"""
    return client.complete_json(SYSTEM_PROMPT, prompt, max_tokens=1800)


def llm_review_node(state: ReviewState) -> ReviewState:
    client = LLMClient(model=state.get("model"))
    errors = list(state.get("errors", []))

    summaries: Dict[str, str] = {}
    llm_findings: List[FileFinding] = []

    per_file_graph = state.get("code_graph_summary", {}).get("per_file", {})

    for path, content in state.get("file_contents", {}).items():
        if not content.strip():
            continue
        graph_info = per_file_graph.get(path, {})
        try:
            result = client.complete_json(
                SYSTEM_PROMPT,
                f"""File: {path}

Code graph context:
{json.dumps(graph_info, indent=2)[:2000]}

Source code:
```
{content[:12000]}
```
{FINDING_SCHEMA_HINT}""",
                max_tokens=1800,
            )
        except Exception as e:  # noqa: BLE001 - surface any provider error, keep pipeline alive
            errors.append(f"LLM review failed for {path}: {e}")
            continue

        if result.get("_parse_error"):
            errors.append(f"LLM returned non-JSON for {path}; skipped.")
            continue

        summaries[path] = result.get("summary", "")
        for f in result.get("findings", []):
            llm_findings.append({
                "file": path,
                "line": f.get("line", 0) or 0,
                "end_line": f.get("line", 0) or 0,
                "rule_id": f"llm-{f.get('category', 'bug')}",
                "cwe": f.get("cwe", []) or [],
                "owasp": [],
                "severity": f.get("severity", "WARNING"),
                "message": f.get("message", ""),
                "source": "llm",
                "code_snippet": "",
            })

    return {
        **state,
        "llm_file_summaries": summaries,
        "llm_findings": llm_findings,
        "errors": errors,
    }
