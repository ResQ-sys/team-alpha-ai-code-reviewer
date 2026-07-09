"""Safe, root-scoped tools the ReAct agent can invoke.

Every tool takes the active workspace ``root`` as its first argument and confines
**all** filesystem access to that root via
:func:`secure_dev.safe_path.resolve_within` — a traversal / absolute / symlink
escape raises :class:`~secure_dev.safe_path.PathEscapeError`, which each tool
turns into a compact error result rather than letting it reach the caller.

Tools return small, JSON-friendly values (strings for read-only inspection, dicts
for mutations) so the agent loop can drop them straight into the model transcript
and the SSE event stream without further shaping.

Public contract (consumed by :mod:`agent.loop`)::

    list_dir(root, rel=".")           -> str
    read_file(root, rel)              -> str
    search(root, query)               -> str
    edit_file(root, rel, find, replace) -> dict  # {applied, diff, added, removed, error}
    create_file(root, rel, content)   -> dict     # {applied, diff, added, removed, error}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

import config
from observability.logging_setup import get_logger
from secure_dev.patch import apply_fix_to_file, make_unified_diff
from secure_dev.safe_path import PathEscapeError, resolve_within

logger = get_logger("agent.tools")

# Directories never surfaced to the agent (VCS metadata, build/venv junk). Mirrors
# the IDE file tree's skip list so the agent sees the same project the user does.
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", ".pytest_cache"}

# Cap on how many directory entries / search hits a single tool call returns, so
# a huge directory or a pathological query cannot flood the model context.
_MAX_DIR_ENTRIES = 200
_MAX_SEARCH_RESULTS = 50

# ripgrep wall-clock budget (seconds); a hung/misbehaving rg is killed and the
# tool falls back to a bounded python walk.
_RG_TIMEOUT = 10.0


def _diff_stats(diff: str) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts from a unified diff.

    Counts body lines only: ``+``/``-`` prefixed lines that are not the
    ``+++``/``---`` file headers.
    """
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _err(message: str) -> dict:
    """Shape a mutation-tool failure as a uniform result dict."""
    return {"applied": False, "diff": "", "added": 0, "removed": 0, "error": message}


# ---------------------------------------------------------------------------
# Read-only inspection tools
# ---------------------------------------------------------------------------
def list_dir(root: str, rel: str = ".") -> str:
    """List the entries of ``rel`` under ``root`` (directories marked with ``/``).

    Junk directories (``.git``, ``node_modules`` …) are omitted. Output is capped
    at :data:`_MAX_DIR_ENTRIES` entries.
    """
    target = rel or "."
    try:
        abs_path = resolve_within(root, target)
    except PathEscapeError as exc:
        return f"error: {exc}"
    if not os.path.isdir(abs_path):
        return f"error: not a directory: {target}"

    try:
        entries = sorted(os.listdir(abs_path))
    except OSError as exc:
        return f"error: could not list {target}: {exc}"

    lines: list[str] = []
    for name in entries:
        if name in _SKIP_DIRS:
            continue
        marker = "/" if os.path.isdir(os.path.join(abs_path, name)) else ""
        lines.append(f"{name}{marker}")
        if len(lines) >= _MAX_DIR_ENTRIES:
            lines.append(f"... (truncated at {_MAX_DIR_ENTRIES} entries)")
            break
    if not lines:
        return f"(empty directory: {target})"
    return "\n".join(lines)


def read_file(root: str, rel: str, max_bytes: Optional[int] = None) -> str:
    """Return the contents of ``rel`` under ``root``, truncated to ``max_bytes``.

    ``max_bytes`` defaults to :data:`config.AGENT_MAX_FILE_BYTES`. Files larger
    than the cap are read up to the cap and marked truncated so the agent still
    gets useful context without pulling an unbounded blob into memory.
    """
    cap = config.AGENT_MAX_FILE_BYTES if max_bytes is None else max_bytes
    try:
        abs_path = resolve_within(root, rel)
    except PathEscapeError as exc:
        return f"error: {exc}"
    if not os.path.isfile(abs_path):
        return f"error: file not found: {rel}"
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(cap + 1)
    except OSError as exc:
        return f"error: could not read {rel}: {exc}"
    if len(data) > cap:
        return data[:cap] + f"\n... (truncated at {cap} bytes)"
    return data


def search(root: str, query: str, max_results: int = _MAX_SEARCH_RESULTS) -> str:
    """Search the workspace for ``query`` and return ``path:line:text`` hits.

    Prefers ripgrep (``rg --json``) for speed and .gitignore awareness; falls
    back to a bounded pure-python walk when ``rg`` is unavailable or errors.
    Results are capped at ``max_results``.
    """
    query = (query or "").strip()
    if not query:
        return "error: empty query"

    rg = shutil.which("rg")
    if rg:
        hits = _search_ripgrep(rg, root, query, max_results)
        if hits is not None:
            return "\n".join(hits) if hits else f"(no matches for {query!r})"
    hits = _search_python(root, query, max_results)
    return "\n".join(hits) if hits else f"(no matches for {query!r})"


def _search_ripgrep(
    rg: str, root: str, query: str, max_results: int
) -> Optional[list[str]]:
    """Run ``rg --json`` under ``root``; return hit lines, or None on failure."""
    try:
        proc = subprocess.run(
            [rg, "--json", "--", query, "."],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_RG_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ripgrep failed, falling back to python walk: %s", exc)
        return None

    hits: list[str] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        path = (data.get("path") or {}).get("text", "?")
        lineno = data.get("line_number", 0)
        text = (data.get("lines") or {}).get("text", "").rstrip("\n")
        hits.append(f"{path}:{lineno}:{text.strip()}")
        if len(hits) >= max_results:
            hits.append(f"... (truncated at {max_results} matches)")
            break
    return hits


def _search_python(root: str, query: str, max_results: int) -> list[str]:
    """Bounded pure-python substring search fallback."""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if query in line:
                            hits.append(f"{rel}:{lineno}:{line.strip()}")
                            if len(hits) >= max_results:
                                hits.append(f"... (truncated at {max_results} matches)")
                                return hits
            except OSError:
                continue
    return hits


# ---------------------------------------------------------------------------
# Mutation tools
# ---------------------------------------------------------------------------
def edit_file(root: str, rel: str, find: str, replace: str) -> dict:
    """Replace ``find`` with ``replace`` in ``rel`` under ``root``.

    Delegates to :func:`secure_dev.patch.apply_fix_to_file`, which resolves the
    path within ``root``, performs an exact-then-fuzzy match, writes the file on
    success and returns a real unified diff. Returns
    ``{applied, diff, added, removed, error}``.
    """
    patch = apply_fix_to_file(root, rel, find, replace)
    added, removed = _diff_stats(patch.diff)
    return {
        "applied": patch.applied,
        "diff": patch.diff,
        "added": added,
        "removed": removed,
        "error": patch.error,
    }


def create_file(root: str, rel: str, content: str) -> dict:
    """Create (or overwrite) ``rel`` under ``root`` with ``content``.

    The path is confined to ``root`` via :func:`resolve_within`; parent
    directories are created as needed. Returns ``{applied, diff, added, removed,
    error}`` where ``diff`` is the whole-file unified diff.
    """
    try:
        abs_path = resolve_within(root, rel)
    except PathEscapeError as exc:
        return _err(f"unsafe path rejected: {exc}")
    if os.path.isdir(abs_path):
        return _err(f"path is a directory: {rel}")

    original = ""
    if os.path.isfile(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                original = fh.read()
        except OSError as exc:
            return _err(f"could not read existing file: {exc}")

    try:
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return _err(f"could not write file: {exc}")

    diff = make_unified_diff(rel, original, content)
    added, removed = _diff_stats(diff)
    return {"applied": True, "diff": diff, "added": added, "removed": removed, "error": None}
