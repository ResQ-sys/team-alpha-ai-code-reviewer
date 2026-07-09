"""
Shared state object passed between every node in the LangGraph pipeline.

Mirrors the architecture from the hackathon problem statement
"AI Software Code Reviewer & Secure Development Agent":

    Repo Ingestion -> Static & Semantic Analysis -> Code Understanding (Code LLM)
    -> Vulnerability Detection -> Secure-Coding RAG -> Review Generation
    -> Verifier / Self-Reflection Agent -> Human-in-the-Loop Approval -> Report
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class FileFinding(TypedDict, total=False):
    file: str
    line: int
    end_line: int
    rule_id: str
    cwe: List[str]
    owasp: List[str]
    severity: str          # INFO | WARNING | ERROR | CRITICAL
    message: str
    source: str            # "semgrep" | "llm" | "heuristic"
    code_snippet: str


class SuggestedFix(TypedDict, total=False):
    file: str
    line: int
    finding_ref: str       # rule_id or synthetic id this fix addresses
    original_code: str
    fixed_code: str
    explanation: str
    verified: bool
    verification_notes: str


class ReviewState(TypedDict, total=False):
    # ---- run configuration ----
    use_rag: bool          # toggle secure-coding RAG retrieval (default True)
    use_redis: bool        # toggle Redis LLM-response caching (default True)

    # ---- ingestion ----
    repo_path: str
    files: List[str]                 # discovered source files
    file_contents: Dict[str, str]     # path -> content
    languages: Dict[str, str]         # path -> detected language

    # ---- static & semantic analysis ----
    semgrep_findings: List[FileFinding]
    code_graph_summary: Dict[str, Any]   # lightweight call/dependency graph stats

    # ---- code understanding (Code LLM) ----
    llm_file_summaries: Dict[str, str]
    llm_findings: List[FileFinding]

    # ---- merged vulnerability detection ----
    merged_findings: List[FileFinding]
    quality_issues: List[FileFinding]

    # ---- secure coding RAG ----
    retrieved_guidance: Dict[str, List[str]]   # finding_ref -> guidance snippets

    # ---- review generation ----
    suggested_fixes: List[SuggestedFix]
    review_comments: List[Dict[str, Any]]

    # ---- verifier / self-reflection ----
    verifier_report: Dict[str, Any]
    confidence_score: float

    # ---- human-in-the-loop ----
    requires_approval: List[Dict[str, Any]]
    approved_fixes: List[Dict[str, Any]]
    rejected_fixes: List[Dict[str, Any]]
    auto_approve: bool

    # ---- final report ----
    quality_score: float
    final_report_md: str
    final_report_json: Dict[str, Any]

    # ---- misc ----
    errors: List[str]
