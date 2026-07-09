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

    # Batch mode: the app drives the pipeline one chunk of files at a time and
    # passes exactly which files this run should cover. When set, honour it
    # verbatim and skip discovery/capping.
    subset = state.get("file_subset")
    if subset:
        files = list(subset)
    else:
        files = discover_source_files(repo_path)

        # User-selectable cap (set from the Streamlit sidebar). Each file becomes
        # one LLM review call — and its findings become more calls — so a large
        # repo on a slow local model is hours of work. 0/absent means review
        # everything. Deterministic (sorted) so the same subset is picked.
        discovered = len(files)
        max_files = state.get("max_files") or 0
        if max_files and discovered > max_files:
            files = files[:max_files]
            errors.append(
                f"Repository has {discovered} source files; reviewing the first "
                f"{max_files} per the 'Max files to review' setting "
                f"(increase it to cover more).")

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
