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
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from agents.state import FileFinding, ReviewState
from config import MAX_LLM_WORKERS
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


def _review_one(client: LLMClient, path: str, content: str, graph_info: Dict,
                rel_path: str) -> Dict:
    """Review a single file. Returns a result dict (never raises) so one bad
    file can't sink the whole batch when run in a thread pool.

    The prompt uses ``rel_path`` (repo-relative) rather than the absolute
    on-disk path so it's identical across fresh clones of the same repo — that
    keeps the LLM-response cache hits working (the abs temp-clone path would
    otherwise change every run and bust the cache)."""
    # Normalize the file path inside the graph context too, for the same reason.
    graph_for_prompt = {**graph_info, "file": rel_path} if graph_info.get("file") else graph_info
    try:
        result = client.complete_json(
            SYSTEM_PROMPT,
            f"""File: {rel_path}

Code graph context:
{json.dumps(graph_for_prompt, indent=2)[:2000]}

Source code:
```
{content[:12000]}
```
{FINDING_SCHEMA_HINT}""",
            max_tokens=1800,
        )
    except Exception as e:  # noqa: BLE001 - surface any provider error, keep pipeline alive
        return {"path": path, "error": f"LLM review failed for {path}: {e}"}

    if result.get("_parse_error"):
        return {"path": path, "error": f"LLM returned non-JSON for {path}; skipped."}
    return {"path": path, "result": result}


def llm_review_node(state: ReviewState) -> ReviewState:
    client = LLMClient()
    errors = list(state.get("errors", []))
    repo_path = state.get("repo_path", "")

    summaries: Dict[str, str] = {}
    llm_findings: List[FileFinding] = []

    per_file_graph = state.get("code_graph_summary", {}).get("per_file", {})

    # One LLM call per file, fanned out across a thread pool. Calls are
    # independent and I/O-bound, so this turns an N-file review from N
    # sequential round-trips into ceil(N / workers) waves.
    targets = [(p, c) for p, c in state.get("file_contents", {}).items() if c.strip()]
    workers = max(1, min(MAX_LLM_WORKERS, len(targets)))

    def _rel(p: str) -> str:
        try:
            return os.path.relpath(p, repo_path) if repo_path else p
        except ValueError:
            return p

    outcomes = []
    if targets:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_review_one, client, path, content,
                            per_file_graph.get(path, {}), _rel(path))
                for path, content in targets
            ]
            for fut in as_completed(futures):
                outcomes.append(fut.result())

    # Merge results deterministically (sorted by path) so output order doesn't
    # depend on thread completion timing.
    for out in sorted(outcomes, key=lambda o: o["path"]):
        if out.get("error"):
            errors.append(out["error"])
            continue
        path, result = out["path"], out["result"]
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
