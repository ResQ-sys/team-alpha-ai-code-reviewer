"""
Streamlit dashboard for the AI Software Code Reviewer & Secure Development
Agent. Lets a reviewer point at a repo path, run the multi-agent pipeline,
inspect findings/fixes, and approve or reject pending high-risk fixes
(Human-in-the-Loop).

Run with: streamlit run app.py
"""
import json

import streamlit as st

from agents.report_agent import report_node
from config import OLLAMA_MODEL, REDIS_ENABLED, USE_RAG
from graph import build_graph
from utils.redis_cache import get_cache, set_enabled

st.set_page_config(page_title="AI Code Reviewer & Secure Dev Agent", layout="wide")
st.title("🛡️ AI Software Code Reviewer & Secure Development Agent")
st.caption("Multi-agent pipeline: ingestion → static analysis → Code-LLM review → "
           "vulnerability merge → secure-coding RAG → fix generation → verifier → "
           "human approval → report")

with st.sidebar:
    repo_path = st.text_input("Repository path", value="./sample_repo")

    st.markdown("### Pipeline options")
    use_rag = st.toggle(
        "Use secure-coding RAG",
        value=USE_RAG,
        help="Ground fixes in retrieved OWASP/CWE guidance. Turn off to compare "
             "ungrounded generation.",
    )
    use_redis = st.toggle(
        "Use Redis cache",
        value=REDIS_ENABLED,
        help="Cache LLM responses so repeat runs are instant.",
    )

    st.caption(f"**Model:** `{OLLAMA_MODEL}`")

    # Live Redis status
    set_enabled(use_redis)
    if use_redis:
        stats = get_cache().stats()
        if stats["available"]:
            st.caption(f"🟢 Redis: `{stats['backend']}` — {stats['keys']} keys, "
                       f"{stats['hits']} hits / {stats['misses']} misses")
        else:
            st.caption("🔴 Redis: unavailable (running without cache)")
    else:
        st.caption("⚪ Redis: disabled")

    run_btn = st.button("Run Review Pipeline", type="primary")

if "result" not in st.session_state:
    st.session_state.result = None

if run_btn:
    spinner_msg = "Running multi-agent review pipeline"
    spinner_msg += " (RAG on)" if use_rag else " (RAG off)"
    with st.spinner(spinner_msg + "..."):
        set_enabled(use_redis)
        app = build_graph()
        state = {
            "repo_path": repo_path,
            "auto_approve": True,
            "use_rag": use_rag,
            "use_redis": use_redis,
            "errors": [],
        }
        st.session_state.result = app.invoke(state)

result = st.session_state.result

if result:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality Score", f"{result['quality_score']}/100")
    c2.metric("Confidence Score", result["confidence_score"])
    c3.metric("Findings", len(result.get("merged_findings", [])))
    c4.metric("Pending Approval", len(result.get("requires_approval", [])))

    rag_badge = "🟢 RAG grounded" if result.get("use_rag", True) else "⚪ RAG off (ungrounded)"
    st.caption(f"{rag_badge}  •  Model: `{OLLAMA_MODEL}`  •  "
               f"Redis cache: {'on' if result.get('use_redis', True) else 'off'}")

    cats = result.get("category_breakdown", {})
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Syntax errors", cats.get("syntax", 0))
    b2.metric("Logical bugs", cats.get("logic", 0))
    b3.metric("Security vulns", cats.get("security", 0))
    b4.metric("Quality issues", cats.get("quality", 0))

    tabs = st.tabs([
        "Vulnerability Report", "Suggested Fixes", "Quality Issues",
        "Human Approval", "Agent Trace", "Raw JSON", "Markdown Report",
    ])

    with tabs[0]:
        for f in result.get("merged_findings", []):
            with st.expander(f"[{f['severity']}] {f['file']}:{f['line']} — {f['rule_id']}"):
                st.write(f["message"])
                if f.get("cwe"):
                    st.caption("CWE: " + ", ".join(f["cwe"]))

    with tabs[1]:
        for fix in result.get("suggested_fixes", []):
            status = "✅ Verified" if fix["verified"] else "⚠️ Flagged"
            with st.expander(f"{fix['file']}:{fix['line']} — {status}"):
                st.write(fix["explanation"])
                col_a, col_b = st.columns(2)
                col_a.code(fix["original_code"], language="python")
                col_b.code(fix["fixed_code"], language="python")
                if fix["verification_notes"]:
                    st.caption(f"Verifier notes: {fix['verification_notes']}")

    with tabs[2]:
        for q in result.get("quality_issues", []):
            st.write(f"- [{q['severity']}] {q['file']}:{q['line']} — {q['message']}")

    with tabs[3]:
        st.subheader("Pending human approval")
        pending = result.get("requires_approval", [])
        if not pending:
            st.success("Nothing pending — all fixes were auto-approved or already actioned.")
        for i, item in enumerate(pending):
            with st.expander(f"{item['file']}:{item['line']} [{item['severity']}]"):
                st.write(item["explanation"])
                col_y, col_n = st.columns(2)
                if col_y.button("Approve", key=f"approve_{i}"):
                    result["approved_fixes"].append(item)
                    result["requires_approval"].remove(item)
                    st.session_state.result = report_node(result)
                    st.rerun()
                if col_n.button("Reject", key=f"reject_{i}"):
                    result["rejected_fixes"].append(item)
                    result["requires_approval"].remove(item)
                    st.session_state.result = report_node(result)
                    st.rerun()

        st.subheader("Approved fixes")
        for item in result.get("approved_fixes", []):
            st.write(f"✅ {item['file']}:{item['line']} [{item['severity']}]")

    with tabs[4]:
        st.subheader("Per-agent trace (observability)")
        trace = result.get("trace", [])
        summary = result.get("final_report_json", {}).get("trace_summary", {})
        if summary:
            t1, t2, t3 = st.columns(3)
            t1.metric("Nodes", summary.get("nodes", 0))
            t2.metric("Total time (ms)", summary.get("total_ms", 0))
            t3.metric("Errors", summary.get("errors", 0))
        if trace:
            st.dataframe(
                [{
                    "#": s["seq"], "agent": s["node"],
                    "duration_ms": s["duration_ms"], "status": s["status"],
                    "produced": ", ".join(f"{k}={v}" for k, v in s.get("produced", {}).items()),
                } for s in trace],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("No trace recorded.")

    with tabs[5]:
        st.json(result.get("final_report_json", {}))
        st.download_button(
            "Download JSON report",
            data=json.dumps(result.get("final_report_json", {}), indent=2),
            file_name="code_review_report.json",
        )

    with tabs[6]:
        st.markdown(result.get("final_report_md", ""))
        st.download_button(
            "Download Markdown report",
            data=result.get("final_report_md", ""),
            file_name="code_review_report.md",
        )

    if result.get("errors"):
        st.warning("Pipeline notices:\n" + "\n".join(f"- {e}" for e in result["errors"]))
else:
    st.info("Enter a repository path and click **Run Review Pipeline** to begin.")
