"""
Agent tracing / observability.

Wraps each LangGraph node so every run records a span — node name, sequence,
start/end time, duration, status (ok/error) and an output delta — into
`state["trace"]` (always on; surfaced in the report and Streamlit).

On top of that in-process trace, spans can be exported to a real external
tracer, selected with the TRACING_BACKEND env var (see config.py):

  none      in-process only (default)
  console   print each span
  otel      OpenTelemetry — OTLP export to Phoenix/Jaeger (set
            OTEL_EXPORTER_OTLP_ENDPOINT) or a console span exporter otherwise
  langsmith one LangSmith run per node (needs LANGSMITH_API_KEY)

Everything degrades gracefully: if a backend or its package is unavailable the
pipeline still runs with the in-process trace.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from config import TRACING_BACKEND

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


# ---------------------------------------------------------------------------
# External exporters (lazy, opt-in, fail-safe)
# ---------------------------------------------------------------------------
_otel = {"init": False, "tracer": None, "provider": None}
_langsmith = {"init": False, "client": None}


def _otel_tracer():
    if _otel["init"]:
        return _otel["tracer"]
    _otel["init"] = True
    try:
        import os
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        provider = TracerProvider(resource=Resource.create({"service.name": "ai-code-reviewer"}))
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        else:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        _otel["provider"] = provider
        _otel["tracer"] = provider.get_tracer("ai-code-reviewer")
    except Exception:
        _otel["tracer"] = None
    return _otel["tracer"]


def _langsmith_client():
    if _langsmith["init"]:
        return _langsmith["client"]
    _langsmith["init"] = True
    try:
        from langsmith import Client
        _langsmith["client"] = Client()
    except Exception:
        _langsmith["client"] = None
    return _langsmith["client"]


def emit_span(span: dict) -> None:
    """Forward a completed span to the configured external tracer."""
    backend = TRACING_BACKEND
    if backend == "none":
        return

    if backend == "console":
        produced = ", ".join(f"{k}={v}" for k, v in span.get("produced", {}).items())
        print(f"[trace] {span['seq']:>2} {span['node']:<18} "
              f"{span['duration_ms']:>8.1f}ms  {span['status']}  {produced}")
        return

    if backend == "otel":
        tracer = _otel_tracer()
        if tracer is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode
            s = tracer.start_span(f"agent.{span['node']}", start_time=span.get("start_ns"))
            s.set_attribute("agent.name", span["node"])
            s.set_attribute("agent.seq", span["seq"])
            s.set_attribute("agent.duration_ms", span["duration_ms"])
            for k, v in span.get("produced", {}).items():
                s.set_attribute(f"produced.{k}", v)
            if span["status"] == "error":
                s.set_status(Status(StatusCode.ERROR, span.get("error", "")))
            s.end(end_time=span.get("end_ns"))
        except Exception:
            pass
        return

    if backend == "langsmith":
        client = _langsmith_client()
        if client is None:
            return
        try:
            from datetime import datetime as _dt
            client.create_run(
                name=f"agent.{span['node']}",
                run_type="chain",
                inputs={"seq": span["seq"]},
                outputs=span.get("produced", {}),
                error=span.get("error"),
                start_time=_dt.fromtimestamp(span["start_ns"] / 1e9, tz=timezone.utc),
                end_time=_dt.fromtimestamp(span["end_ns"] / 1e9, tz=timezone.utc),
                project_name="ai-code-reviewer",
            )
        except Exception:
            pass


def shutdown_tracing() -> None:
    """Flush any buffered spans (call once at the end of a run)."""
    if _otel["provider"] is not None:
        try:
            _otel["provider"].force_flush()
            _otel["provider"].shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
def traced(name: str, fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """Wrap a node function so its execution is recorded as a span in
    `state["trace"]` and exported to the configured backend."""

    def wrapper(state: dict) -> dict:
        started_at = datetime.now(timezone.utc).isoformat()
        start_ns = time.time_ns()
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
        end_ns = time.time_ns()

        prev_trace = result.get("trace", state.get("trace", [])) or []
        after = _counts(result)
        produced = {k: after[k] for k in after if after.get(k, 0) != before.get(k, 0)}

        span = {
            "node": name,
            "seq": len(prev_trace) + 1,
            "started_at": started_at,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "duration_ms": duration_ms,
            "status": status,
            "produced": produced,
        }
        if error:
            span["error"] = error

        emit_span(span)

        # keep the in-state span lean (drop raw ns timestamps)
        state_span = {k: v for k, v in span.items() if k not in ("start_ns", "end_ns")}
        new_state = {**result, "trace": prev_trace + [state_span]}
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
