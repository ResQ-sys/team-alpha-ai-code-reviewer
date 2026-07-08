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

    findings, semgrep_errors = run_semgrep(repo_path)
    errors.extend(semgrep_errors)

    file_graphs = []
    for path, content in state.get("file_contents", {}).items():
        lang = state.get("languages", {}).get(path, "unknown")
        file_graphs.append(build_code_graph(path, content, lang))

    graph_summary = summarize_repo_graph(file_graphs)
    graph_summary["per_file"] = {g["file"]: g for g in file_graphs}

    return {
        **state,
        "semgrep_findings": findings,
        "code_graph_summary": graph_summary,
        "errors": errors,
    }
