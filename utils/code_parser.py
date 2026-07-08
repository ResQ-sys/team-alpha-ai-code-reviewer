"""
Lightweight code structure extraction.

Full Graph Neural Network training (as hinted at in the problem statement's
"Techniques" list) is out of scope for a hackathon-timeline demo. Instead
this module builds a *code graph summary* — function/class definitions and
a naive call graph — using Python's `ast` module for .py files and regex
heuristics for other languages. This summary is passed to the LLM review
node so it reasons over structure rather than raw text alone, and stands in
as the "Graph Neural Networks (Code Graph Representation)" component.

Swap-in point for production: replace `build_code_graph` with an actual
GNN over a tree-sitter-derived AST (e.g. using `code2graph` / `CodeBERT`
graph encodings).
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List

from config import SUPPORTED_EXTENSIONS


def detect_language(path: str) -> str:
    ext = os.path.splitext(path)[1]
    return SUPPORTED_EXTENSIONS.get(ext, "unknown")


def discover_source_files(repo_path: str) -> List[str]:
    files = []
    for root, _dirs, filenames in os.walk(repo_path):
        if any(part in root for part in (".git", "node_modules", "__pycache__", ".venv")):
            continue
        for fn in filenames:
            if os.path.splitext(fn)[1] in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, fn))
    return sorted(files)


def _python_graph(source: str) -> Dict:
    """Extract functions, classes, calls, and import edges from Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": f"SyntaxError: {e}", "functions": [], "classes": [], "calls": [], "imports": []}

    functions, classes, calls, imports = [], [], [], []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions.append({"name": node.name, "line": node.lineno, "args": [a.arg for a in node.args.args]})
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno})
        elif isinstance(node, ast.Call):
            fn_name = None
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr
            if fn_name:
                calls.append({"name": fn_name, "line": node.lineno})
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.append(alias.name)

    # Flag dangerous sinks often relevant to security review (used later by
    # the vulnerability agent as heuristic corroboration for LLM findings).
    dangerous_sinks = {"eval", "exec", "os.system", "subprocess", "pickle.loads", "yaml.load", "md5", "sha1"}
    flagged_calls = [c for c in calls if c["name"] in dangerous_sinks or any(c["name"] == d.split(".")[-1] for d in dangerous_sinks)]

    return {
        "functions": functions,
        "classes": classes,
        "call_count": len(calls),
        "imports": sorted(set(imports)),
        "flagged_calls": flagged_calls,
    }


def _generic_graph(source: str) -> Dict:
    """Regex heuristics for non-Python languages (JS/TS/Java/Go/etc.)."""
    functions = re.findall(r"(?:function|def|func)\s+([A-Za-z0-9_]+)\s*\(", source)
    classes = re.findall(r"class\s+([A-Za-z0-9_]+)", source)
    dangerous_patterns = re.findall(
        r"\b(eval|exec|system|Runtime\.exec|md5|sha1|innerHTML|dangerouslySetInnerHTML|unserialize)\s*\(",
        source,
    )
    return {
        "functions": [{"name": f, "line": None} for f in functions],
        "classes": [{"name": c, "line": None} for c in classes],
        "call_count": None,
        "imports": [],
        "flagged_calls": [{"name": p, "line": None} for p in dangerous_patterns],
    }


def build_code_graph(path: str, source: str, language: str) -> Dict:
    if language == "python":
        graph = _python_graph(source)
    else:
        graph = _generic_graph(source)
    graph["file"] = path
    graph["language"] = language
    graph["loc"] = source.count("\n") + 1
    return graph


def summarize_repo_graph(file_graphs: List[Dict]) -> Dict:
    total_functions = sum(len(g.get("functions", [])) for g in file_graphs)
    total_classes = sum(len(g.get("classes", [])) for g in file_graphs)
    total_loc = sum(g.get("loc", 0) for g in file_graphs)
    flagged = [
        {"file": g["file"], "pattern": c["name"], "line": c.get("line")}
        for g in file_graphs
        for c in g.get("flagged_calls", [])
    ]
    return {
        "total_files": len(file_graphs),
        "total_loc": total_loc,
        "total_functions": total_functions,
        "total_classes": total_classes,
        "flagged_dangerous_calls": flagged,
    }
