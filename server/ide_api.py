"""IDE-companion HTTP surface, mounted onto the existing review app.

This module extends the FastAPI application built by :func:`server.api.create_app`
so a *single* server process serves both the code-review pipeline routes and the
lightweight IDE routes (file browser, editor, Ollama-backed chat / inline edit).

Endpoints added here
--------------------
* ``GET  /api/models``          -> ``{"models": [name, ...]}`` (proxies Ollama ``/api/tags``)
* ``GET  /api/fs/tree?root=``   -> nested :class:`FileNode` (paths relative to the workspace root)
* ``GET  /api/fs/file?path=``   -> ``{path, content, language}``
* ``PUT  /api/fs/file``         -> ``{ok: true}`` (guarded write)
* ``POST /api/chat``            -> SSE stream of assistant tokens (Ollama ``/api/chat``)
* ``POST /api/edit``            -> SSE stream of the rewritten code only

Security
--------
Every filesystem path is confined to the workspace root via
:func:`secure_dev.safe_path.resolve_within`, which canonicalizes both sides
(collapsing ``..`` and symlinks) and refuses any target that escapes the root.
Traversal / absolute inputs raise :class:`~secure_dev.safe_path.PathEscapeError`,
surfaced as HTTP 400.

Ollama access is funnelled through module-level helpers (:func:`_list_ollama_models`,
:func:`_stream_ollama_chat`) built on :mod:`requests`; tests monkeypatch
``requests.get`` / ``requests.post`` to exercise parsing without a live server.
The chat / edit streams degrade gracefully when Ollama is unreachable, emitting an
SSE ``event: error`` frame instead of crashing the response.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from typing import Iterator, Optional
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
from agent.loop import request_cancel, run_agent
from observability.events import CLOSE, get_bus
from observability.logging_setup import get_logger
from secure_dev.safe_path import PathEscapeError, resolve_within
from server.api import create_app

logger = get_logger("server.ide_api")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Directories never surfaced in the file tree (VCS metadata, build/venv junk).
_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist"}

# Hard cap on file-tree recursion depth. A backstop alongside the per-branch
# symlink-cycle guard so a pathological (or maliciously deep) directory layout
# cannot blow the Python stack.
_MAX_TREE_DEPTH = 64

# Default workspace root; overridable per-app (tests) or via env.
_DEFAULT_WORKSPACE_ROOT = os.getenv(
    "IDE_WORKSPACE_ROOT", "/Users/pranav/Documents/team-alpha-ai-code-reviewer"
)

# HTTP timeouts for Ollama calls (seconds). Streaming gets a generous read budget.
_OLLAMA_LIST_TIMEOUT = 5.0
_OLLAMA_STREAM_TIMEOUT = (5.0, 120.0)  # (connect, read)

# Agent-events SSE pacing: poll the subscriber queue this often, and close a
# stream after this many seconds of IDLE time (no events). The deadline is reset
# on every event, so a long-but-active agent run is never falsely timed out; only
# a genuinely stalled stream is reaped so a client cannot hang a worker forever.
_SSE_POLL_SECONDS = 0.1
try:
    _SSE_TIMEOUT_SECONDS = float(os.getenv("SSE_IDLE_TIMEOUT_SECONDS", "600"))
except ValueError:
    _SSE_TIMEOUT_SECONDS = 600.0

# Cap on how many bytes ``GET /api/fs/file`` will pull into memory. A single
# request for a large in-workspace file (log, model weight, media asset) would
# otherwise load the whole thing into RAM and then into a JSON response — a
# trivial memory-exhaustion DoS. Files above this are rejected with HTTP 413.
MAX_READ_BYTES = 2_000_000

# Extension -> Monaco/Highlight.js language id.
_LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rs": "rust",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sh": "bash",
    ".sql": "sql",
    ".xml": "xml",
    ".txt": "plaintext",
}


def _ollama_base() -> str:
    """Return the Ollama base URL (scheme://host:port), no trailing slash.

    Honours ``OLLAMA_BASE_URL`` first, then derives from ``config.OLLAMA_ENDPOINT``
    (which points at ``.../api/generate``), finally falling back to localhost.
    """
    explicit = os.getenv("OLLAMA_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    parts = urlsplit(config.OLLAMA_ENDPOINT or "")
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return "http://localhost:11434"


OLLAMA_BASE = _ollama_base()
DEFAULT_MODEL = config.OLLAMA_MODEL


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class FileWriteRequest(BaseModel):
    """Body for ``PUT /api/fs/file``."""

    path: str = Field(..., min_length=1, description="Workspace-relative file path.")
    content: str = Field(default="", description="Full new file contents.")


class WorkspaceRequest(BaseModel):
    """Body for ``POST /api/workspace`` — open a local directory as the root."""

    path: str = Field(..., min_length=1, description="Absolute path to a local directory.")


class AgentRequest(BaseModel):
    """Body for ``POST /api/agent`` — start an agent run over the active root."""

    task: str = Field(..., min_length=1, description="What the agent should do.")
    model: Optional[str] = None


class ChatMessage(BaseModel):
    """A single chat turn."""

    role: str = Field(..., description="'system' | 'user' | 'assistant'.")
    content: str = Field(default="")


class ChatRequest(BaseModel):
    """Body for ``POST /api/chat``."""

    messages: list[ChatMessage] = Field(default_factory=list)
    model: Optional[str] = None
    file_context: Optional[str] = None


class EditRequest(BaseModel):
    """Body for ``POST /api/edit``."""

    code: str = Field(..., description="Code to rewrite.")
    instruction: str = Field(..., min_length=1, description="What to change.")
    language: Optional[str] = None
    model: Optional[str] = None


class PullRequest(BaseModel):
    """Body for ``POST /api/models/pull``."""

    name: str = Field(..., min_length=1, description="Ollama model name to pull.")


# ---------------------------------------------------------------------------
# Ollama proxy helpers (monkeypatched in tests via requests.get / requests.post)
# ---------------------------------------------------------------------------
def _list_ollama_models() -> list[str]:
    """Return available model names from Ollama's ``/api/tags``.

    Returns an empty list on any failure (Ollama down, bad JSON, timeout) so the
    IDE degrades to "no models" rather than erroring the whole page.
    """
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=_OLLAMA_LIST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - best-effort proxy; never propagate.
        logger.warning("failed to list Ollama models", exc_info=True)
        return []

    models = payload.get("models", []) if isinstance(payload, dict) else []
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if name:
                names.append(name)
        elif isinstance(entry, str):
            names.append(entry)
    return names


def _stream_ollama_chat(model: str, messages: list[dict]) -> Iterator[str]:
    """Yield assistant content tokens from Ollama's streaming ``/api/chat``.

    Ollama returns newline-delimited JSON objects, each shaped like
    ``{"message": {"role": "assistant", "content": "<token>"}, "done": false}``
    with a final ``{"done": true}`` sentinel. Malformed lines are skipped.

    Raises whatever :func:`requests.post` raises on a connection failure — the
    caller wraps iteration to emit an SSE error frame.
    """
    resp = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json={"model": model, "messages": messages, "stream": True},
        stream=True,
        timeout=_OLLAMA_STREAM_TIMEOUT,
    )
    resp.raise_for_status()
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        message = obj.get("message") or {}
        token = message.get("content") if isinstance(message, dict) else None
        if token:
            yield token
        if obj.get("done"):
            break


def _sse_data(text: str) -> str:
    """Frame a token as an SSE ``data:`` event."""
    return f"data: {text}\n\n"


def _sse_json(payload: dict) -> str:
    """Frame a dict as a single JSON SSE ``data:`` event."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _sse_error(detail: str) -> str:
    """Frame an error as an SSE ``event: error`` frame."""
    return f"event: error\ndata: {detail}\n\n"


def _stream_completion(model: str, messages: list[dict]) -> Iterator[str]:
    """Shared SSE generator for chat + edit: tokens, graceful error, then DONE."""
    try:
        for token in _stream_ollama_chat(model, messages):
            yield _sse_data(token)
    except Exception:  # noqa: BLE001 - surface as an SSE error, not a 500.
        # Log internals server-side; the client gets a generic message so the
        # Ollama base URL / connection topology never reaches the browser.
        logger.warning("Ollama chat stream failed", exc_info=True)
        yield _sse_error("the model backend is unavailable")
    yield _sse_data("[DONE]")


def _stream_pull(name: str) -> Iterator[str]:
    """SSE generator that proxies Ollama ``/api/pull`` download progress.

    Ollama streams newline-delimited JSON like
    ``{"status":"pulling manifest"}`` / ``{"status":"downloading","completed":N,
    "total":M}`` / ``{"status":"success"}``. We forward each as a compact JSON
    SSE frame (status + percent) so the UI can show a live progress bar, then a
    terminal ``[DONE]``. Failures become a generic SSE error (never leak the
    Ollama host).
    """
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/pull",
            json={"name": name, "stream": True},
            stream=True,
            timeout=_OLLAMA_STREAM_TIMEOUT,
        )
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            status = str(obj.get("status", ""))
            completed = obj.get("completed")
            total = obj.get("total")
            percent = None
            if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total:
                percent = round(completed / total * 100, 1)
            frame: dict = {"status": status}
            if percent is not None:
                frame["percent"] = percent
            if obj.get("error"):
                frame["error"] = "pull failed"
            yield _sse_json(frame)
            if status == "success" or obj.get("error"):
                break
    except Exception:  # noqa: BLE001 - surface as SSE error, not a 500.
        logger.warning("Ollama pull stream failed for %r", name, exc_info=True)
        yield _sse_error("could not pull the model")
    yield _sse_data("[DONE]")


def _language_for(path: str) -> str:
    """Infer a language id from a file extension (defaults to plaintext)."""
    return _LANGUAGE_BY_EXT.get(os.path.splitext(path)[1].lower(), "plaintext")


# ---------------------------------------------------------------------------
# Filesystem tree
# ---------------------------------------------------------------------------
def _build_tree(
    root: str,
    abs_path: str,
    rel_path: str,
    _seen: frozenset[str] = frozenset(),
    _depth: int = 0,
) -> dict:
    """Recursively build a :class:`FileNode` rooted at ``abs_path``.

    ``rel_path`` is the node's path *relative to the workspace root* (empty for
    the root node). Every child is re-validated with :func:`resolve_within` so an
    in-tree symlink pointing *outside* the workspace is silently pruned.

    Cycle safety: :func:`resolve_within` only enforces containment, so an in-tree
    symlink whose target resolves back to an ancestor still *inside* the root
    (e.g. ``loop -> .``) passes the guard and would otherwise recurse forever. We
    defend against this by tracking the real paths of the ancestors on the
    current branch (``_seen``) and refusing to descend into a directory we have
    already entered, plus a hard depth cap.
    """
    name = os.path.basename(abs_path.rstrip(os.sep)) or os.path.basename(root.rstrip(os.sep))

    if not os.path.isdir(abs_path):
        return {"name": name, "path": rel_path, "type": "file", "children": None}

    real = os.path.realpath(abs_path)
    if real in _seen or _depth >= _MAX_TREE_DEPTH:
        # Symlink cycle back onto an ancestor, or too deep: stop descending and
        # present it as an empty directory rather than recursing without bound.
        return {"name": name, "path": rel_path, "type": "directory", "children": []}
    branch_seen = _seen | {real}

    children: list[dict] = []
    try:
        entries = sorted(os.listdir(abs_path))
    except OSError:
        entries = []

    for entry in entries:
        if entry in _SKIP_DIRS:
            continue
        child_rel = os.path.join(rel_path, entry) if rel_path else entry
        try:
            child_abs = resolve_within(root, child_rel)
        except PathEscapeError:
            # Symlink or crafted name escaping the workspace: skip it.
            continue
        children.append(
            _build_tree(root, child_abs, child_rel, branch_seen, _depth + 1)
        )

    # Directories first, then files; each group alphabetical.
    children.sort(key=lambda n: (n["type"] == "file", n["name"].lower()))
    return {"name": name, "path": rel_path, "type": "directory", "children": children}


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_ide_app(
    store=None,
    *,
    workspace_root: Optional[str] = None,
    **create_app_kwargs,
) -> FastAPI:
    """Build the review app and register the IDE routes on the same instance.

    Args:
        store: Optional persistence backend forwarded to
            :func:`server.api.create_app` (tests inject an in-memory store).
        workspace_root: Root every filesystem path is confined to. Defaults to
            ``IDE_WORKSPACE_ROOT`` / the repo root. Canonicalized on entry.
        **create_app_kwargs: Extra kwargs forwarded to ``create_app``
            (``allowed_root``, ``api_key``, ``job_runner``).

    Returns:
        A single :class:`fastapi.FastAPI` app serving review + IDE routes.
    """
    app = create_app(store, **create_app_kwargs)
    root = os.path.realpath(workspace_root or _DEFAULT_WORKSPACE_ROOT)
    # The active workspace root is DYNAMIC: seeded here but replaceable at runtime
    # via POST /api/workspace. Every filesystem helper reads it back off
    # app.state (through _active_root) so opening a new folder retargets the file
    # tree, editor and agent atomically — while each path op still funnels through
    # resolve_within(active_root, rel) for containment.
    app.state.ide_workspace_root = root
    # Set of live agent run_ids (for stop() validation).
    app.state.agent_runs = set()

    def _active_root() -> str:
        """Return the currently-open workspace root (dynamic)."""
        return app.state.ide_workspace_root

    # Mirror the review app's auth: the same shared secret that gates the
    # state-changing review routes must also gate the (more dangerous) IDE
    # routes — arbitrary workspace file read/write and Ollama-backed chat/edit.
    # Resolve it the same way create_app does: explicit kwarg wins, else config
    # (empty string = auth disabled, preserving the open-by-default dev setup).
    _api_key_kw = create_app_kwargs.get("api_key")
    expected_key = _api_key_kw if _api_key_kw is not None else config.API_KEY
    app.state.ide_api_key = expected_key

    def _require_api_key(x_api_key: Optional[str]) -> None:
        """Reject IDE calls when a key is configured but not matched (401)."""
        if expected_key and x_api_key != expected_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def _resolve(rel: str) -> str:
        """Confine ``rel`` to the *active* workspace root or raise HTTP 400."""
        try:
            return resolve_within(_active_root(), rel)
        except PathEscapeError as exc:
            # Log the specifics; return a generic message so the guard cannot be
            # used to probe host paths / workspace layout.
            logger.warning("rejected unsafe path: %s", exc)
            raise HTTPException(
                status_code=400, detail="path is outside the workspace"
            ) from exc

    # -------------------------------------------------------------------
    # Workspace (open any local repo as the active root)
    # -------------------------------------------------------------------
    @app.get("/api/workspace")
    def get_workspace(x_api_key: Optional[str] = Header(default=None)) -> dict:
        """Return the currently-open workspace ``{root, name}``."""
        # Gated like the rest of the IDE surface: the absolute host root path is
        # itself sensitive, so it must not be readable without the shared secret.
        _require_api_key(x_api_key)
        active = _active_root()
        return {"root": active, "name": os.path.basename(active.rstrip(os.sep))}

    @app.post("/api/workspace")
    def set_workspace(
        req: WorkspaceRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> dict:
        """Open a local directory as the active workspace root.

        Opening a folder is an explicit user action in this single-user local
        tool, so any *existing* directory is accepted; per-file access remains
        containment-guarded under whichever root is active. Gated by the same
        shared secret as the other state-changing IDE routes.
        """
        _require_api_key(x_api_key)
        candidate = (req.path or "").strip()
        if not candidate:
            raise HTTPException(status_code=400, detail="path must not be blank")
        real = os.path.realpath(candidate)
        if not os.path.isdir(real):
            raise HTTPException(
                status_code=400, detail="path does not exist or is not a directory"
            )
        app.state.ide_workspace_root = real
        logger.info("active workspace root set to %s", real)
        return {"root": real, "name": os.path.basename(real.rstrip(os.sep))}

    # -------------------------------------------------------------------
    # Agent mode (ReAct loop over the active workspace)
    # -------------------------------------------------------------------
    @app.post("/api/agent")
    def start_agent(
        req: AgentRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> dict:
        """Start an agent run over the active workspace; return its ``run_id``.

        The run executes on a daemon thread and streams thought/action/edit/
        summary events to the :class:`EventBus` keyed by ``run_id``; the client
        follows them via ``GET /api/agent/{run_id}/events``.
        """
        _require_api_key(x_api_key)
        run_id = uuid.uuid4().hex
        active = _active_root()
        app.state.agent_runs.add(run_id)

        def _worker() -> None:
            try:
                run_agent(req.task, active, run_id, model=req.model)
            finally:
                app.state.agent_runs.discard(run_id)

        thread = threading.Thread(
            target=_worker, name=f"agent-run-{run_id}", daemon=True
        )
        thread.start()
        logger.info("started agent run %s over %s", run_id, active)
        return {"run_id": run_id}

    @app.get("/api/agent/{run_id}/events")
    def agent_events(
        run_id: str,
        x_api_key: Optional[str] = Header(default=None),
        api_key: Optional[str] = Query(default=None),
    ) -> StreamingResponse:
        """Stream an agent run's events as SSE (mirrors ``/api/jobs/{id}/events``).

        Subscribes to the shared :class:`EventBus`, forwards every published
        event as a ``data: <json>\\n\\n`` frame, and terminates with
        ``data: [DONE]\\n\\n`` when the run closes the stream. The bus replays
        buffered events so a late subscriber still sees the full history; a
        keepalive + hard deadline guarantee the stream always ends.

        Gated by the same shared secret as ``start``/``stop``. Browser
        ``EventSource`` clients cannot set headers, so the key may also be passed
        as an ``?api_key=`` query parameter.
        """
        _require_api_key(x_api_key if x_api_key is not None else api_key)

        def _stream() -> Iterator[str]:
            bus = get_bus()
            subscriber = bus.subscribe(run_id)
            # IDLE deadline: reset on every real event (below) so only a stalled
            # stream is reaped, never a long-but-active run.
            deadline = time.monotonic() + _SSE_TIMEOUT_SECONDS
            try:
                while True:
                    try:
                        item = subscriber.get(timeout=_SSE_POLL_SECONDS)
                    except queue.Empty:
                        if time.monotonic() > deadline:
                            yield _sse_json(
                                {"type": "status", "status": "error", "message": "timeout"}
                            )
                            break
                        yield ": keepalive\n\n"
                        continue
                    # Real event: push the idle deadline forward before handling.
                    deadline = time.monotonic() + _SSE_TIMEOUT_SECONDS
                    if item is CLOSE:
                        break
                    yield _sse_json(item)
            finally:
                bus.unsubscribe(run_id, subscriber)
            yield "data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @app.post("/api/agent/{run_id}/stop")
    def stop_agent(
        run_id: str, x_api_key: Optional[str] = Header(default=None)
    ) -> dict:
        """Request cancellation of an agent run; the loop stops at its next step."""
        _require_api_key(x_api_key)
        request_cancel(run_id)
        logger.info("cancellation requested for agent run %s", run_id)
        return {"ok": True}

    @app.get("/api/models")
    def models(x_api_key: Optional[str] = Header(default=None)) -> dict:
        """List locally available Ollama models (empty on failure)."""
        _require_api_key(x_api_key)
        return {"models": _list_ollama_models()}

    @app.post("/api/models/pull")
    def pull_model(
        req: PullRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> StreamingResponse:
        """Pull an Ollama model by name, streaming download progress over SSE.

        Lets the user bring *any* Ollama model (not just what is already
        installed): the UI posts the model name and renders the live progress
        frames until ``success`` / ``[DONE]``, then refreshes the model list.
        """
        _require_api_key(x_api_key)
        return StreamingResponse(
            _stream_pull(req.name.strip()), media_type="text/event-stream"
        )

    @app.get("/api/fs/tree")
    def fs_tree(
        root_param: str = Query(default="", alias="root"),
        x_api_key: Optional[str] = Header(default=None),
    ) -> dict:
        """Return the workspace file tree rooted at ``?root=`` (relative path)."""
        # Gate the recursive tree walk with the same shared secret as the rest of
        # the fs/* surface: an unauthenticated caller must not be able to
        # enumerate the workspace (or reach the walker) when a key is configured.
        _require_api_key(x_api_key)
        active = _active_root()
        cleaned = (root_param or "").strip()
        if cleaned in ("", "."):
            base_abs = active
            base_rel = ""
        else:
            base_abs = _resolve(cleaned)
            base_rel = os.path.relpath(base_abs, active)
            if base_rel == ".":
                base_rel = ""
        if not os.path.isdir(base_abs):
            raise HTTPException(status_code=404, detail="root is not a directory")
        try:
            return _build_tree(active, base_abs, base_rel)
        except (OSError, RecursionError):
            # Defensive: the cycle/depth guards above should prevent runaway
            # recursion, but never leak a stack trace / host path to the client.
            logger.warning("failed to build file tree for %s", base_abs, exc_info=True)
            raise HTTPException(status_code=500, detail="failed to build file tree")

    @app.get("/api/fs/file")
    def fs_read(path: str, x_api_key: Optional[str] = Header(default=None)) -> dict:
        """Read a workspace file: ``{path, content, language}``."""
        _require_api_key(x_api_key)
        abs_path = _resolve(path)
        if not os.path.isfile(abs_path):
            raise HTTPException(status_code=404, detail="file not found")
        # Reject oversized files before opening so a single request cannot pull
        # an unbounded blob into memory (and then into the JSON response).
        try:
            size = os.path.getsize(abs_path)
        except OSError as exc:
            logger.warning("stat failed for %s", abs_path, exc_info=True)
            raise HTTPException(status_code=500, detail="read failed") from exc
        if size > MAX_READ_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                # Read one byte past the cap to catch races (file grown after
                # stat) and reject rather than buffering the whole thing.
                content = fh.read(MAX_READ_BYTES + 1)
        except OSError as exc:
            logger.warning("read failed for %s", abs_path, exc_info=True)
            raise HTTPException(status_code=500, detail="read failed") from exc
        if len(content) > MAX_READ_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        return {"path": path, "content": content, "language": _language_for(path)}

    @app.put("/api/fs/file")
    def fs_write(
        req: FileWriteRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> dict:
        """Write a workspace file (guarded against traversal), creating parents."""
        _require_api_key(x_api_key)
        abs_path = _resolve(req.path)
        if os.path.isdir(abs_path):
            raise HTTPException(status_code=400, detail="path is a directory")
        parent = os.path.dirname(abs_path)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(req.content)
        except OSError as exc:
            logger.warning("write failed for %s", abs_path, exc_info=True)
            raise HTTPException(status_code=500, detail="write failed") from exc
        return {"ok": True}

    @app.post("/api/chat")
    def chat(
        req: ChatRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> StreamingResponse:
        """Stream a coding-assistant reply as SSE tokens."""
        _require_api_key(x_api_key)
        model = req.model or DEFAULT_MODEL
        system = (
            "You are an expert coding assistant embedded in an IDE. Give precise, "
            "concise, technically correct help. Prefer minimal, idiomatic code."
        )
        if req.file_context:
            system += (
                "\n\nThe user is currently editing this file; use it as context:\n"
                f"{req.file_context}"
            )
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m.role, "content": m.content} for m in req.messages]
        return StreamingResponse(
            _stream_completion(model, messages), media_type="text/event-stream"
        )

    @app.post("/api/edit")
    def edit(
        req: EditRequest, x_api_key: Optional[str] = Header(default=None)
    ) -> StreamingResponse:
        """Stream ONLY the rewritten code as SSE tokens."""
        _require_api_key(x_api_key)
        model = req.model or DEFAULT_MODEL
        system = "Return only the edited code, no fences, no prose."
        lang = req.language or "plaintext"
        user = (
            f"Language: {lang}\n"
            f"Instruction: {req.instruction}\n\n"
            f"Code to edit:\n{req.code}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return StreamingResponse(
            _stream_completion(model, messages), media_type="text/event-stream"
        )

    return app


# Module-level app for ``uvicorn server.ide_api:app`` — one server, both surfaces.
app = create_ide_app()
