"""
LangGraph StateGraph wiring for the AI Software Code Reviewer & Secure
Development Agent pipeline.

    ingestion -> static_analysis -> llm_review -> vulnerability
             -> rag -> review_generation -> verifier -> approval -> report

Every node is wrapped by :func:`_instrument` so that — when the incoming state
carries a ``job_id`` — it publishes a ``running`` event before the node runs and
a ``done`` event (with a meaningful per-node ``detail`` summary) after, onto the
process-wide :class:`observability.events.EventBus`. This is what lets the HTTP
layer stream live agent activity. With no ``job_id`` present (e.g. direct
``build_graph().invoke(...)`` in tests), instrumentation is a no-op and behaviour
is identical to the un-instrumented pipeline.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from langgraph.graph import END, StateGraph

from agents.approval_agent import approval_node
from agents.ingestion_agent import ingestion_node
from agents.llm_review_agent import llm_review_node
from agents.rag_agent import rag_node
from agents.report_agent import report_node
from agents.state import ReviewState
from agents.static_analysis_agent import static_analysis_node
from agents.verifier_agent import verifier_node
from agents.review_generation_agent import review_generation_node
from agents.vulnerability_agent import vulnerability_node
from observability.events import get_bus
from observability.logging_setup import get_logger

logger = get_logger("graph")

# (node_id, human label) for every node, IN PIPELINE ORDER. The ids/labels are
# the shared backend<->frontend event contract; do not rename casually.
_NODE_META: list[tuple[str, str]] = [
    ("ingestion", "Ingestion"),
    ("static_analysis", "Static Analysis (Semgrep)"),
    ("llm_review", "Code-LLM Review"),
    ("vulnerability", "Vulnerability Merge"),
    ("rag", "Secure-Coding RAG"),
    ("review_generation", "Fix Generation"),
    ("verifier", "Verifier / Self-Reflection"),
    ("approval", "Human-Approval Gate"),
    ("report", "Report"),
]


def _summarize(node_id: str, result: Any) -> str:
    """Derive a short, human-meaningful ``detail`` string from a node's output.

    ``result`` is the (partial) state dict the node returned. Every accessor is
    defensive — a missing/oddly-typed key yields a sane fallback rather than
    raising inside the instrumentation wrapper.
    """
    if not isinstance(result, dict):
        return "done"

    def _n(key: str) -> int:
        value = result.get(key)
        try:
            return len(value)  # type: ignore[arg-type]
        except TypeError:
            return 0

    if node_id == "ingestion":
        return f"{_n('files')} files ingested"
    if node_id == "static_analysis":
        return f"{_n('semgrep_findings')} issues found"
    if node_id == "llm_review":
        return f"{_n('llm_file_summaries')} files reviewed"
    if node_id == "vulnerability":
        return f"{_n('merged_findings')} merged findings"
    if node_id == "rag":
        guidance = result.get("retrieved_guidance") or {}
        docs = 0
        if isinstance(guidance, dict):
            for snippets in guidance.values():
                try:
                    docs += len(snippets)
                except TypeError:
                    continue
        return f"{docs} guidance docs"
    if node_id == "review_generation":
        return f"{_n('suggested_fixes')} fixes drafted"
    if node_id == "verifier":
        report = result.get("verifier_report") or {}
        verified = report.get("verified_fixes", 0) if isinstance(report, dict) else 0
        flagged = report.get("flagged_fixes", 0) if isinstance(report, dict) else 0
        return f"verified {verified}, flagged {flagged}"
    if node_id == "approval":
        return f"{_n('requires_approval')} pending approval"
    if node_id == "report":
        score = result.get("quality_score", 0)
        try:
            score = round(float(score))
        except (TypeError, ValueError):
            score = 0
        return f"quality {score}/100"
    return "done"


def _instrument(node_id: str, label: str, fn: Callable[[ReviewState], Any]):
    """Wrap ``fn`` so it emits ``running``/``done`` agent events when job_id is set.

    Publishing is best-effort: any bus failure is logged and swallowed so
    observability can never break the pipeline itself.
    """

    def wrapped(state: ReviewState):
        job_id = state.get("job_id") if isinstance(state, dict) else None
        bus = get_bus() if job_id else None

        if bus is not None:
            try:
                bus.publish(
                    job_id,
                    {"type": "agent", "node": node_id, "label": label, "status": "running"},
                )
            except Exception:  # noqa: BLE001 - observability must not break the run.
                logger.warning("failed to publish running event for %s", node_id, exc_info=True)

        result = fn(state)

        if bus is not None:
            try:
                bus.publish(
                    job_id,
                    {
                        "type": "agent",
                        "node": node_id,
                        "label": label,
                        "status": "done",
                        "detail": _summarize(node_id, result),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.warning("failed to publish done event for %s", node_id, exc_info=True)

        return result

    wrapped.__name__ = getattr(fn, "__name__", node_id)
    return wrapped


# node_id -> raw node function.
_NODE_FUNCS: dict[str, Callable[[ReviewState], Any]] = {
    "ingestion": ingestion_node,
    "static_analysis": static_analysis_node,
    "llm_review": llm_review_node,
    "vulnerability": vulnerability_node,
    "rag": rag_node,
    "review_generation": review_generation_node,
    "verifier": verifier_node,
    "approval": approval_node,
    "report": report_node,
}


def build_graph():
    """Compile the instrumented review pipeline into a runnable LangGraph app."""
    graph = StateGraph(ReviewState)

    for node_id, label in _NODE_META:
        graph.add_node(node_id, _instrument(node_id, label, _NODE_FUNCS[node_id]))

    graph.set_entry_point("ingestion")
    graph.add_edge("ingestion", "static_analysis")
    graph.add_edge("static_analysis", "llm_review")
    graph.add_edge("llm_review", "vulnerability")
    graph.add_edge("vulnerability", "rag")
    graph.add_edge("rag", "review_generation")
    graph.add_edge("review_generation", "verifier")
    graph.add_edge("verifier", "approval")
    graph.add_edge("approval", "report")
    graph.add_edge("report", END)

    return graph.compile()


def run_pipeline(
    repo_path: str,
    auto_approve: bool = True,
    job_id: Optional[str] = None,
    model: Optional[str] = None,
) -> ReviewState:
    """Run the full pipeline over ``repo_path`` and return the final state.

    When ``job_id`` is provided, it is injected into the initial state so every
    node streams live agent events onto the :class:`~observability.events.EventBus`,
    and a terminal ``status`` event plus :meth:`~observability.events.EventBus.close`
    are emitted at the end (or on error). With ``job_id`` omitted, behaviour is
    identical to the un-instrumented pipeline — no events, no bus interaction.

    When ``model`` is provided it is injected into the initial state so the LLM
    nodes run against the UI-selected model instead of the configured default.
    """
    app = build_graph()
    initial_state: ReviewState = {
        "repo_path": repo_path,
        "auto_approve": auto_approve,
        "errors": [],
    }
    if job_id:
        initial_state["job_id"] = job_id
    if model:
        initial_state["model"] = model

    bus = get_bus() if job_id else None
    try:
        result = app.invoke(initial_state)
    except Exception:
        if bus is not None:
            try:
                bus.publish(job_id, {"type": "status", "status": "error"})
                bus.close(job_id)
            except Exception:  # noqa: BLE001
                logger.warning("failed to emit error/close for job %s", job_id, exc_info=True)
        raise

    if bus is not None:
        try:
            bus.publish(job_id, {"type": "status", "status": "done"})
            bus.close(job_id)
        except Exception:  # noqa: BLE001
            logger.warning("failed to emit done/close for job %s", job_id, exc_info=True)

    return result
