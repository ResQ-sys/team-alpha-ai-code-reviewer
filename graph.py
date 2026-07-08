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


def build_graph():
    graph = StateGraph(ReviewState)

    graph.add_node("ingestion", ingestion_node)
    graph.add_node("static_analysis", static_analysis_node)
    graph.add_node("llm_review", llm_review_node)
    graph.add_node("vulnerability", vulnerability_node)
    graph.add_node("rag", rag_node)
    graph.add_node("review_generation", review_generation_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("approval", approval_node)
    graph.add_node("report", report_node)

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


def run_pipeline(repo_path: str, auto_approve: bool = True) -> ReviewState:
    app = build_graph()
    initial_state: ReviewState = {
        "repo_path": repo_path,
        "auto_approve": auto_approve,
        "errors": [],
    }
    result = app.invoke(initial_state)
    return result
