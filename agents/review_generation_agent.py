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
from typing import List

from agents.state import ReviewState, SuggestedFix
from utils.llm_client import LLMClient

SYSTEM_PROMPT = """You are an AI pair-programmer generating a code review \
comment and a concrete fix for a specific security/quality finding. Ground \
your fix in the provided secure-coding guidance. Keep fixes minimal and \
targeted — do not rewrite unrelated code. Explain your reasoning in plain \
language a mid-level developer can follow."""


def _build_prompt(finding, code_snippet: str, guidance: List[str]) -> str:
    guidance_text = "\n".join(f"- {g}" for g in guidance) or "(no specific guidance retrieved; use general secure-coding best practice)"
    return f"""Finding:
File: {finding['file']}
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


def review_generation_node(state: ReviewState) -> ReviewState:
    client = LLMClient(model=state.get("model"))
    errors = list(state.get("errors", []))
    guidance_map = state.get("retrieved_guidance", {})

    suggested_fixes: List[SuggestedFix] = []
    review_comments: List[dict] = []

    for finding in state.get("merged_findings", []):
        ref = f"{finding['file']}:{finding.get('line', 0)}:{finding.get('rule_id', '')}"
        snippet = _get_snippet(state, finding)
        guidance = guidance_map.get(ref, [])

        try:
            result = client.complete_json(SYSTEM_PROMPT, _build_prompt(finding, snippet, guidance), max_tokens=1200)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Review generation failed for {ref}: {e}")
            continue

        if result.get("_parse_error"):
            errors.append(f"Review generation returned non-JSON for {ref}")
            continue

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
