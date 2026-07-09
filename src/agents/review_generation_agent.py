"""
Review Generation Agent
------------------------
For each merged security/bug finding, asks the Code-LLM to produce:
  - an explainable review comment (why this matters, in plain language)
  - a concrete suggested fix, grounded in the retrieved secure-coding
    guidance (Code RAG) rather than free-form generation.

Covers "Generate explainable code reviews, automated fixes, and secure
coding recommendations" from the Objective list, and the "Explainable AI"
technique.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from agents.state import ReviewState, SuggestedFix
from config import MAX_LLM_WORKERS
from utils.llm_client import LLMClient

SYSTEM_PROMPT = """You are an AI pair-programmer generating a code review \
comment and a concrete fix for a specific security/quality finding. Ground \
your fix in the provided secure-coding guidance. Keep fixes minimal and \
targeted — do not rewrite unrelated code. Explain your reasoning in plain \
language a mid-level developer can follow."""


def _build_prompt(finding, code_snippet: str, guidance: List[str], rel_file: str) -> str:
    # Use the repo-relative path (not the absolute temp-clone path) so the
    # prompt — and therefore the LLM-response cache key — is stable across fresh
    # clones of the same repo.
    guidance_text = "\n".join(f"- {g}" for g in guidance) or "(no specific guidance retrieved; use general secure-coding best practice)"
    return f"""Finding:
File: {rel_file}
Line: {finding.get('line')}
Severity: {finding.get('severity')}
Rule: {finding.get('rule_id')}
Message: {finding.get('message')}

Relevant code:
```
{code_snippet}
```

Retrieved secure-coding guidance:
{guidance_text}

Return JSON with this exact shape:
{{
  "explanation": "<why this is a problem, plain language, 2-4 sentences>",
  "fixed_code": "<minimal corrected code snippet, same language>",
  "confidence": <float 0-1>
}}
"""


def _get_snippet(state: ReviewState, finding) -> str:
    if finding.get("code_snippet"):
        return finding["code_snippet"]
    content = state.get("file_contents", {}).get(finding["file"], "")
    lines = content.splitlines()
    line = finding.get("line", 1) or 1
    start = max(0, line - 3)
    end = min(len(lines), line + 2)
    return "\n".join(lines[start:end])


def _generate_one(client: LLMClient, finding, snippet: str, guidance: List[str],
                  rel_file: str) -> dict:
    """Generate a review comment + fix for one finding. Never raises."""
    ref = f"{finding['file']}:{finding.get('line', 0)}:{finding.get('rule_id', '')}"
    try:
        result = client.complete_json(
            SYSTEM_PROMPT, _build_prompt(finding, snippet, guidance, rel_file),
            max_tokens=1200)
    except Exception as e:  # noqa: BLE001
        return {"ref": ref, "error": f"Review generation failed for {ref}: {e}"}
    if result.get("_parse_error"):
        return {"ref": ref, "error": f"Review generation returned non-JSON for {ref}"}
    return {"ref": ref, "finding": finding, "snippet": snippet,
            "guidance": guidance, "result": result}


def review_generation_node(state: ReviewState) -> ReviewState:
    client = LLMClient()
    errors = list(state.get("errors", []))
    guidance_map = state.get("retrieved_guidance", {})

    suggested_fixes: List[SuggestedFix] = []
    review_comments: List[dict] = []

    findings = state.get("merged_findings", [])
    workers = max(1, min(MAX_LLM_WORKERS, len(findings) or 1))
    repo_path = state.get("repo_path", "")

    def _rel(p: str) -> str:
        try:
            return os.path.relpath(p, repo_path) if repo_path else p
        except ValueError:
            return p

    # One independent LLM call per finding — fan them out across the pool.
    outcomes = []
    if findings:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for finding in findings:
                ref = f"{finding['file']}:{finding.get('line', 0)}:{finding.get('rule_id', '')}"
                futures.append(pool.submit(
                    _generate_one, client, finding,
                    _get_snippet(state, finding), guidance_map.get(ref, []),
                    _rel(finding.get("file", ""))))
            for fut in as_completed(futures):
                outcomes.append(fut.result())

    # Deterministic ordering regardless of thread completion timing.
    for out in sorted(outcomes, key=lambda o: o["ref"]):
        if out.get("error"):
            errors.append(out["error"])
            continue
        finding, snippet, guidance = out["finding"], out["snippet"], out["guidance"]
        result, ref = out["result"], out["ref"]

        review_comments.append({
            "finding_ref": ref,
            "file": finding["file"],
            "line": finding.get("line"),
            "severity": finding.get("severity"),
            "message": finding.get("message"),
            "explanation": result.get("explanation", ""),
            "guidance_used": guidance,
        })

        suggested_fixes.append({
            "file": finding["file"],
            "line": finding.get("line", 0),
            "finding_ref": ref,
            "original_code": snippet,
            "fixed_code": result.get("fixed_code", ""),
            "explanation": result.get("explanation", ""),
            "verified": False,
            "verification_notes": "",
        })

    return {
        **state,
        "suggested_fixes": suggested_fixes,
        "review_comments": review_comments,
        "errors": errors,
    }
