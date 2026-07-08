"""
Ingestion Agent
---------------
First node in the pipeline. Walks the target repository, discovers source
files across supported languages, reads their contents, and detects
language per file. Corresponds to "Understand repository structure and
software dependencies" in the Requirements list.
"""
from __future__ import annotations

from agents.state import ReviewState
from utils.code_parser import detect_language, discover_source_files


def ingestion_node(state: ReviewState) -> ReviewState:
    repo_path = state["repo_path"]
    errors = list(state.get("errors", []))

    files = discover_source_files(repo_path)
    file_contents = {}
    languages = {}

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                file_contents[path] = f.read()
            languages[path] = detect_language(path)
        except OSError as e:
            errors.append(f"Could not read {path}: {e}")

    if not files:
        errors.append(f"No supported source files found under {repo_path}")

    return {
        **state,
        "files": files,
        "file_contents": file_contents,
        "languages": languages,
        "errors": errors,
    }
