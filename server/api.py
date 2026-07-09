"""FastAPI application for the AI code-review & secure-development pipeline.

This module is the network entrypoint. It wraps the LangGraph pipeline
(:func:`graph.run_pipeline`) behind a small REST + SSE surface and persists all
work through :class:`persistence.store.Store`.

Endpoints
---------
* ``GET  /api/health``                  -> liveness probe
* ``POST /api/analyze``                  -> queue a review (runs in a background thread)
* ``GET  /api/jobs/{job_id}``            -> full job record (404 if missing)
* ``GET  /api/jobs``                     -> recent jobs
* ``GET  /api/jobs/{job_id}/events``     -> SSE stream of status transitions
* ``POST /api/approvals``                -> persist human-approval decisions
* ``POST /api/apply``                    -> apply suggested fixes (dry-run by default)

Design
------
* :func:`create_app` is a factory so tests can inject a fake/in-memory
  :class:`Store`. A module-level ``app = create_app()`` is provided for
  ``uvicorn server.api:app``.
* Analysis runs on a background daemon thread; the shared :class:`Store`
  (thread-safe) is the single source of truth for status and results.
* :func:`graph.run_pipeline` and ``secure_dev.fix_applier.apply_fixes`` are
  referenced *by module* (never imported by value) so tests can monkeypatch
  them without the real pipeline / LLM / network ever running.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import queue
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
import graph
from observability.events import CLOSE, get_bus
from observability.logging_setup import get_logger
from persistence.store import Store

logger = get_logger("server.api")

# Browser origins allowed to call the API (Vite / CRA / Streamlit dev servers).
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8501",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8501",
]

# Statuses at which the analysis run is finished and SSE streaming should stop.
_TERMINAL_STATUSES = {"completed", "error"}

# SSE polling cadence and an IDLE timeout (seconds of no events) after which a
# stalled stream is closed. Env-configurable; applied as an idle deadline that is
# reset on every event, so a long-but-active review is never falsely cut off.
_SSE_POLL_SECONDS = 0.1
try:
    _SSE_TIMEOUT_SECONDS = float(os.getenv("SSE_IDLE_TIMEOUT_SECONDS", "120"))
except ValueError:
    _SSE_TIMEOUT_SECONDS = 120.0


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    """Body for ``POST /api/analyze``."""

    repo_path: str = Field(..., min_length=1, description="Path to the repo to review.")
    model: Optional[str] = Field(
        default=None, description="Optional UI-selected model; overrides the server default."
    )


class ApprovalRequest(BaseModel):
    """Body for ``POST /api/approvals``."""

    job_id: str = Field(..., min_length=1)
    decisions: dict = Field(default_factory=dict)


class ApplyRequest(BaseModel):
    """Body for ``POST /api/apply``.

    ``dry_run`` defaults to ``None`` so the endpoint can fall back to
    ``config.APPLY_DRY_RUN_DEFAULT`` (fail-safe: dry-run) rather than baking a
    less-safe default into the wire contract. Callers may still pass an explicit
    ``true``/``false``.
    """

    job_id: str = Field(..., min_length=1)
    dry_run: Optional[bool] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_report(result: Any) -> dict:
    """Pull the report dict out of a :func:`graph.run_pipeline` result.

    The pipeline returns a ``ReviewState`` dict whose ``final_report_json`` key
    holds the canonical report. We fall back to the whole result (or a wrapped
    string) so persistence never fails on an unexpected shape.
    """
    if isinstance(result, dict):
        report = result.get("final_report_json")
        if isinstance(report, dict):
            return report
        return result
    return {"result": str(result)}


def _result_to_dict(result: Any) -> dict:
    """Normalise an ``ApplyResult`` (dataclass) / mapping into a plain dict."""
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    raise TypeError(f"cannot serialise apply result of type {type(result)!r}")


def _sse(payload: dict) -> str:
    """Format a dict as a single Server-Sent-Events ``data:`` frame."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _run_pipeline(repo_path: str, job_id: str, model: Optional[str] = None) -> Any:
    """Invoke ``graph.run_pipeline``, threading ``job_id`` / ``model`` when supported.

    The job id is what wires node-level events to the live SSE stream, and
    ``model`` selects the LLM the pipeline runs against. Each is passed only when
    the (possibly monkeypatched) ``run_pipeline`` accepts that parameter, so
    older/test stubs with just ``(repo_path, auto_approve=True)`` still work
    rather than raising ``TypeError``.
    """
    fn = graph.run_pipeline  # module attribute so tests can monkeypatch it.
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins / C funcs without a signature.
        params = {}
    kwargs: dict[str, Any] = {"auto_approve": True}
    if "job_id" in params:
        kwargs["job_id"] = job_id
    if "model" in params and model:
        kwargs["model"] = model
    return fn(repo_path, **kwargs)


def _run_job(
    store: Store, job_id: str, repo_path: str, model: Optional[str] = None
) -> None:
    """Execute the review pipeline for ``job_id`` and persist the outcome.

    Runs on a background thread. Any exception is captured and recorded as an
    ``error`` status rather than propagating (there is no caller to catch it).
    """
    try:
        store.set_status(job_id, "running")
        logger.info("job %s: running pipeline on %s", job_id, repo_path)
        # Referenced via the module so tests can monkeypatch graph.run_pipeline;
        # job_id is injected into the pipeline state to drive live agent events.
        result = _run_pipeline(repo_path, job_id, model)
        store.save_report(job_id, _extract_report(result))
        store.set_status(job_id, "completed")
        logger.info("job %s: completed", job_id)
    except Exception as exc:  # noqa: BLE001 - background boundary: never re-raise.
        logger.exception("job %s: pipeline failed", job_id)
        try:
            store.set_status(job_id, "error", error=str(exc))
        except Exception:  # noqa: BLE001 - store already gone / job vanished.
            logger.exception("job %s: failed to record error status", job_id)


def _validate_repo_path(raw: str, allowed_root: Optional[str]) -> str:
    """Canonicalize and authorize an incoming ``repo_path``.

    ``repo_path`` is attacker-influenced (any HTTP caller): left unchecked it
    lets a request run the pipeline over — and, on a non-dry-run apply, mutate —
    any directory the server process can reach. We resolve it with
    :func:`os.path.realpath` (collapsing ``..`` and symlinks), require it to be
    a real existing directory, and — when an allowlist root is configured —
    confine it to that root or a descendant.

    Returns the canonicalized absolute path (persisted and reused downstream so
    later stages never re-resolve attacker input). Raises ``HTTPException`` with
    a clean status on any violation.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise HTTPException(status_code=422, detail="repo_path must not be blank")

    real = os.path.realpath(candidate)
    if not os.path.isdir(real):
        raise HTTPException(
            status_code=400,
            detail="repo_path does not exist or is not a directory",
        )

    root = (allowed_root or "").strip()
    if root:
        root_real = os.path.realpath(root)
        if not os.path.isdir(root_real):
            raise HTTPException(
                status_code=503,
                detail="server misconfigured: ALLOWED_REPO_ROOT is not a directory",
            )
        if real != root_real and os.path.commonpath([root_real, real]) != root_real:
            raise HTTPException(
                status_code=403,
                detail="repo_path is outside the allowed workspace root",
            )

    return real


def _load_fix_applier():
    """Import ``secure_dev.fix_applier`` lazily.

    The applier is owned by a sibling module and may be monkeypatched in tests
    via ``sys.modules``. Importing lazily keeps this app importable even before
    that module lands and lets tests inject a fake.
    """
    return importlib.import_module("secure_dev.fix_applier")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(
    store: Optional[Store] = None,
    *,
    job_runner: Optional[Callable[..., None]] = None,
    allowed_root: Optional[str] = None,
    api_key: Optional[str] = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        store: Persistence backend. Defaults to a :class:`Store` at
            ``config.DB_PATH``. Tests pass an in-memory store.
        job_runner: Callable that executes a job synchronously; spawned on a
            background thread by ``/api/analyze``. Defaults to :func:`_run_job`.
            Injectable for tests that want deterministic control.
        allowed_root: Workspace root that every ``repo_path`` must resolve
            under. Defaults to ``config.ALLOWED_REPO_ROOT`` (empty = no subtree
            confinement, existence still enforced).
        api_key: Shared secret required (via ``X-API-Key``) on state-changing
            endpoints. Defaults to ``config.API_KEY`` (empty = auth disabled).

    Returns:
        A configured :class:`fastapi.FastAPI` instance.
    """
    active_store = store if store is not None else Store(config.DB_PATH)
    runner = job_runner if job_runner is not None else _run_job
    workspace_root = allowed_root if allowed_root is not None else config.ALLOWED_REPO_ROOT
    expected_key = api_key if api_key is not None else config.API_KEY

    def _require_api_key(x_api_key: Optional[str]) -> None:
        """Reject state-changing calls when a key is configured but not matched."""
        if expected_key and x_api_key != expected_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    app = FastAPI(title="AI Code Reviewer API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Stash the store so tests / other layers can reach it off the app.
    app.state.store = active_store

    @app.get("/api/health")
    def health() -> dict:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/api/analyze")
    def analyze(
        req: AnalyzeRequest,
        x_api_key: Optional[str] = Header(default=None),
    ) -> dict:
        """Queue a review job and return its id; work runs in the background."""
        _require_api_key(x_api_key)
        # Canonicalize + authorize before persisting; downstream stages reuse
        # this resolved path and never re-resolve the raw request value.
        repo_path = _validate_repo_path(req.repo_path, workspace_root)
        job_id = active_store.create_job(repo_path)
        thread = threading.Thread(
            target=runner,
            args=(active_store, job_id, repo_path, req.model),
            name=f"review-job-{job_id}",
            daemon=True,
        )
        thread.start()
        logger.info("queued job %s for %s", job_id, repo_path)
        return {"job_id": job_id}

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> dict:
        """Return recent jobs, most recent first."""
        return {"jobs": active_store.list_jobs(limit=limit)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        """Return a single job record (404 if unknown)."""
        job = active_store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
        return job

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str) -> StreamingResponse:
        """Stream live agent-activity events for a job as SSE.

        Subscribes to the process-wide :class:`~observability.events.EventBus` and
        forwards every event the pipeline publishes (agent running/done, logs,
        job status) as ``data: <json>\\n\\n`` frames, ending with ``data: [DONE]``.
        The bus replays buffered events so a late subscriber still sees the
        history; if the job already finished with no bus activity (e.g. a stubbed
        pipeline) we synthesise a terminal status from the store instead of
        hanging. A keepalive + hard deadline guarantee the stream always ends.
        """
        if active_store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"no such job: {job_id}")

        def _stream():
            # Always announce the stream is live first (job-lifecycle event).
            yield _sse({"type": "status", "status": "running"})

            bus = get_bus()
            subscriber = bus.subscribe(job_id)
            # IDLE deadline: extended on every real event so only a genuinely
            # inactive stream is cut off. A long-but-healthy review that keeps
            # publishing node events is never falsely timed out.
            deadline = time.monotonic() + _SSE_TIMEOUT_SECONDS
            try:
                while True:
                    try:
                        item = subscriber.get(timeout=_SSE_POLL_SECONDS)
                    except queue.Empty:
                        # No event this tick. If the job is already terminal in
                        # the store (bus never published/closed — e.g. a stubbed
                        # pipeline), synthesise the closing status and stop.
                        job = active_store.get_job(job_id)
                        if job and job["status"] in _TERMINAL_STATUSES:
                            frame = {
                                "type": "status",
                                "status": "error" if job["status"] == "error" else "done",
                            }
                            if job.get("error"):
                                frame["message"] = job["error"]
                            yield _sse(frame)
                            break
                        if time.monotonic() > deadline:
                            yield _sse(
                                {"type": "status", "status": "error", "message": "timeout"}
                            )
                            break
                        yield ": keepalive\n\n"
                        continue
                    # A real event arrived: the stream is alive, so push the
                    # idle deadline forward before handling it.
                    deadline = time.monotonic() + _SSE_TIMEOUT_SECONDS
                    if item is CLOSE:
                        break
                    yield _sse(item)
            finally:
                bus.unsubscribe(job_id, subscriber)
            yield "data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @app.post("/api/approvals")
    def approvals(
        req: ApprovalRequest,
        x_api_key: Optional[str] = Header(default=None),
    ) -> dict:
        """Persist human-approval decisions for a job."""
        _require_api_key(x_api_key)
        try:
            active_store.record_approval(req.job_id, req.decisions)
            active_store.set_status(req.job_id, "approved")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/apply")
    def apply(
        req: ApplyRequest,
        x_api_key: Optional[str] = Header(default=None),
    ) -> dict:
        """Apply (or dry-run) the suggested fixes for a completed job."""
        _require_api_key(x_api_key)
        job = active_store.get_job(req.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no such job: {req.job_id}")

        # Fail safe: when the caller omits dry_run, fall back to the configured
        # default (dry-run) rather than silently writing to disk.
        dry_run = config.APPLY_DRY_RUN_DEFAULT if req.dry_run is None else req.dry_run

        report = job.get("report") or {}
        suggested_fixes = report.get("suggested_fixes", []) if isinstance(report, dict) else []

        try:
            fix_applier = _load_fix_applier()
        except ImportError as exc:  # pragma: no cover - defensive.
            raise HTTPException(
                status_code=503, detail=f"fix applier unavailable: {exc}"
            ) from exc

        try:
            result = fix_applier.apply_fixes(
                job["repo_path"], suggested_fixes, dry_run=dry_run
            )
        except Exception as exc:  # noqa: BLE001 - surface as a clean 500.
            # Log full detail server-side; return a generic message so host
            # paths / internals never reach the client.
            logger.exception("apply failed for job %s", req.job_id)
            raise HTTPException(status_code=500, detail="apply failed") from exc

        result_dict = _result_to_dict(result)
        applied = result_dict.get("applied", [])
        active_store.record_applied(req.job_id, applied)
        if not dry_run:
            active_store.set_status(req.job_id, "applied")
        return result_dict

    return app


# Module-level app for ``uvicorn server.api:app``.
app = create_app()
