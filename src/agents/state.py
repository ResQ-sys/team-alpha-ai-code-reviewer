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
    category: str          # syntax | logic | security | quality
    message: str
    source: str            # "semgrep" | "llm" | "parser" | "heuristic"
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
    model_used: str        # Ollama model selected for this run (for display)
    max_files: int         # cap on files sent to the LLM (0/absent = no limit)

    # ---- ingestion ----
    repo_path: str
    file_subset: List[str]           # if set, review ONLY these files (batch mode)
    files: List[str]                 # discovered source files
    file_contents: Dict[str, str]     # path -> content
    languages: Dict[str, str]         # path -> detected language

    # ---- static & semantic analysis ----
    semgrep_findings: List[FileFinding]
    syntax_findings: List[FileFinding]    # parser syntax errors as first-class findings
    code_graph_summary: Dict[str, Any]   # lightweight call/dependency graph stats

    # ---- code understanding (Code LLM) ----
    llm_file_summaries: Dict[str, str]
    llm_findings: List[FileFinding]

    # ---- merged vulnerability detection ----
    merged_findings: List[FileFinding]
    quality_issues: List[FileFinding]
    category_breakdown: Dict[str, int]    # counts per category (syntax/logic/security/quality)

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

    # ---- observability ----
    trace: List[Dict[str, Any]]           # per-node spans (name/seq/duration/status)

    # ---- misc ----
    errors: List[str]
