"""
Static & Semantic Analysis Agent
---------------------------------
Runs Semgrep security rulesets across the repo (static analysis) and builds
a lightweight per-file code graph (function/class/call/import structure +
flagged dangerous sinks) that stands in for the Graph Neural Network /
"Code Graph Representation" component named in the problem statement.
"""
from __future__ import annotations

from agents.state import ReviewState
from utils.code_parser import build_code_graph, summarize_repo_graph
from utils.semgrep_runner import run_semgrep


def static_analysis_node(state: ReviewState) -> ReviewState:
    repo_path = state["repo_path"]
    errors = list(state.get("errors", []))

    # Scope semgrep to exactly the source files we ingested (state["files"]),
    # NOT the whole repo directory. This (a) skips non-code files like Django/
    # Jinja HTML templates that semgrep can't parse — which otherwise flood the
    # notices with "syntax error" noise and can trip semgrep into its minimal
    # fallback ruleset — and (b) keeps batch mode from re-scanning the whole repo.
    targets = state.get("file_subset") or state.get("files") or None
    findings, semgrep_errors = run_semgrep(repo_path, targets=targets)
    errors.extend(semgrep_errors)

    file_graphs = []
    syntax_findings = []
    for path, content in state.get("file_contents", {}).items():
        lang = state.get("languages", {}).get(path, "unknown")
        graph = build_code_graph(path, content, lang)
        file_graphs.append(graph)

        # Promote parser syntax errors to first-class CRITICAL findings so they
        # are surfaced in the report as a distinct category, not just metadata.
        syn = graph.get("syntax_error")
        if syn:
            syntax_findings.append({
                "file": path,
                "line": syn.get("line", 0),
                "end_line": syn.get("line", 0),
                "rule_id": "syntax-error",
                "cwe": [],
                "owasp": [],
                "severity": "CRITICAL",
                "message": f"Syntax error — file does not parse: {syn.get('message', '')}",
                "source": "parser",
                "category": "syntax",
                "code_snippet": "",
            })

    graph_summary = summarize_repo_graph(file_graphs)
    graph_summary["per_file"] = {g["file"]: g for g in file_graphs}

    return {
        **state,
        "semgrep_findings": findings,
        "syntax_findings": syntax_findings,
        "code_graph_summary": graph_summary,
        "errors": errors,
    }
