"""
Lightweight agent tracing / observability.

Wraps each LangGraph node so every run records a span — node name, sequence,
start time, duration, status (ok/error), and a small output delta — into
`state["trace"]`. This gives real per-agent observability (surfaced in the
report JSON/Markdown and the Streamlit dashboard) without pulling in a heavy
tracing backend. It is also the natural hook point for LangSmith/Phoenix/MLflow
exporters (see `emit_span`).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

# Fields whose length we report as a per-node "output delta", so a span shows
# what each agent actually produced.
_COUNTED_FIELDS = (
    "files", "semgrep_findings", "syntax_findings", "llm_findings",
    "merged_findings", "quality_issues", "retrieved_guidance",
    "suggested_fixes", "requires_approval", "approved_fixes",
)


def _counts(state: dict) -> dict:
    out = {}
    for k in _COUNTED_FIELDS:
        v = state.get(k)
        if isinstance(v, (list, dict)):
            out[k] = len(v)
    return out


def emit_span(span: dict) -> None:
    """Extension point: forward a span to an external tracer (LangSmith /
    Phoenix / OpenTelemetry / MLflow). No-op by default."""
    return None


def traced(name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Wrap a node function so its execution is recorded as a span in
    `state["trace"]`."""

    def wrapper(state: dict) -> dict:
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        status = "ok"
        error = ""
        before = _counts(state)
        try:
            result = fn(state)
        except Exception as e:  # noqa: BLE001 - record failure, keep pipeline observable
            status = "error"
            error = f"{type(e).__name__}: {e}"
            result = {**state}
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        prev_trace = result.get("trace", state.get("trace", [])) or []
        after = _counts(result)
        produced = {k: after[k] for k in after if after.get(k, 0) != before.get(k, 0)}

        span = {
            "node": name,
            "seq": len(prev_trace) + 1,
            "started_at": started_at,
            "duration_ms": duration_ms,
            "status": status,
            "produced": produced,
        }
        if error:
            span["error"] = error

        emit_span(span)

        new_state = {**result, "trace": prev_trace + [span]}
        if error:
            new_state["errors"] = list(new_state.get("errors", [])) + [f"{name}: {error}"]
        return new_state

    return wrapper


def summarize_trace(trace: list) -> dict:
    """Roll a trace up into headline observability metrics."""
    if not trace:
        return {"nodes": 0, "total_ms": 0.0, "errors": 0, "slowest": None}
    total = round(sum(s.get("duration_ms", 0) for s in trace), 1)
    errors = sum(1 for s in trace if s.get("status") == "error")
    slowest = max(trace, key=lambda s: s.get("duration_ms", 0))
    return {
        "nodes": len(trace),
        "total_ms": total,
        "errors": errors,
        "slowest": {"node": slowest["node"], "duration_ms": slowest["duration_ms"]},
    }
