"""
Secure-Coding RAG Agent
------------------------
For each merged security finding, retrieves relevant secure-coding
guidance/documentation from the knowledge base (OWASP/CWE-style corpus) so
the review-generation agent can ground its recommendations instead of
hallucinating best practices. Covers "Retrieve secure coding practices and
relevant documentation" from the Requirements list.
"""
from __future__ import annotations

from typing import Dict, List

from agents.state import ReviewState
from rag.knowledge_base import SecureCodingKB

_kb_singleton: SecureCodingKB | None = None


def _get_kb() -> SecureCodingKB:
    global _kb_singleton
    if _kb_singleton is None:
        _kb_singleton = SecureCodingKB()
    return _kb_singleton


def rag_node(state: ReviewState) -> ReviewState:
    # RAG is user-toggleable. When disabled, we skip retrieval entirely and the
    # review-generation agent falls back to general secure-coding best practice.
    if not state.get("use_rag", True):
        errors = list(state.get("errors", []))
        errors.append(
            "RAG disabled — fixes generated without retrieved guidance "
            "(using the model's general secure-coding knowledge)."
        )
        return {**state, "retrieved_guidance": {}, "errors": errors}

    kb = _get_kb()
    retrieved: Dict[str, List[str]] = {}

    for finding in state.get("merged_findings", []):
        ref = f"{finding['file']}:{finding.get('line', 0)}:{finding.get('rule_id', '')}"
        tags = [c.lower() for c in finding.get("cwe", [])] + [finding.get("rule_id", "")]
        docs = kb.query_by_tags(tags)
        if not docs:
            query_text = f"{finding.get('rule_id', '')} {finding.get('message', '')}"
            docs = kb.query(query_text)
        retrieved[ref] = [f"[{d['title']}] {d['text']}" for d in docs]

    return {**state, "retrieved_guidance": retrieved}
