"""
LangGraph StateGraph wiring for the AI Software Code Reviewer & Secure
Development Agent pipeline.

    ingestion -> static_analysis -> llm_review -> vulnerability
             -> rag -> review_generation -> verifier -> approval -> report
"""
from __future__ import annotations

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
from utils.tracing import traced

# Ordered pipeline stages, with human-friendly labels for the UI progress
# display. Order MUST match the edges wired in build_graph().
PIPELINE_NODES = [
    ("ingestion", "Ingesting repository files"),
    ("static_analysis", "Running static analysis (Semgrep + code graph)"),
    ("llm_review", "Code-LLM semantic review"),
    ("vulnerability", "Merging & de-duplicating findings"),
    ("rag", "Retrieving secure-coding guidance (RAG)"),
    ("review_generation", "Generating explainable fixes"),
    ("verifier", "Verifier / self-reflection pass"),
    ("approval", "Applying human-approval policy"),
    ("report", "Assembling final report"),
]


def build_graph():
    graph = StateGraph(ReviewState)

    # Every node is wrapped with `traced(...)` so each run records a span
    # (timing/status/output) into state["trace"] for observability.
    graph.add_node("ingestion", traced("ingestion", ingestion_node))
    graph.add_node("static_analysis", traced("static_analysis", static_analysis_node))
    graph.add_node("llm_review", traced("llm_review", llm_review_node))
    graph.add_node("vulnerability", traced("vulnerability", vulnerability_node))
    graph.add_node("rag", traced("rag", rag_node))
    graph.add_node("review_generation", traced("review_generation", review_generation_node))
    graph.add_node("verifier", traced("verifier", verifier_node))
    graph.add_node("approval", traced("approval", approval_node))
    graph.add_node("report", traced("report", report_node))

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


def run_pipeline(repo_path: str, auto_approve: bool = True,
                 use_rag: bool = True, use_redis: bool = True) -> ReviewState:
    from config import USE_RAG, REDIS_ENABLED
    from utils.redis_cache import set_enabled

    set_enabled(use_redis and REDIS_ENABLED)

    app = build_graph()
    initial_state: ReviewState = {
        "repo_path": repo_path,
        "auto_approve": auto_approve,
        "use_rag": use_rag and USE_RAG,
        "use_redis": use_redis and REDIS_ENABLED,
        "errors": [],
    }
    result = app.invoke(initial_state)
    return result
