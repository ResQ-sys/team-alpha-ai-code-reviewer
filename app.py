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
from graph import build_graph

st.set_page_config(page_title="AI Code Reviewer & Secure Dev Agent", layout="wide")
st.title("🛡️ AI Software Code Reviewer & Secure Development Agent")
st.caption("Multi-agent pipeline: ingestion → static analysis → Code-LLM review → "
           "vulnerability merge → secure-coding RAG → fix generation → verifier → "
           "human approval → report")

with st.sidebar:
    repo_path = st.text_input("Repository path", value="./sample_repo")
    run_btn = st.button("Run Review Pipeline", type="primary")

if "result" not in st.session_state:
    st.session_state.result = None

if run_btn:
    with st.spinner("Running multi-agent review pipeline..."):
        app = build_graph()
        state = {"repo_path": repo_path, "auto_approve": True, "errors": []}
        st.session_state.result = app.invoke(state)

result = st.session_state.result

if result:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality Score", f"{result['quality_score']}/100")
    c2.metric("Confidence Score", result["confidence_score"])
    c3.metric("Findings", len(result.get("merged_findings", [])))
    c4.metric("Pending Approval", len(result.get("requires_approval", [])))

    tabs = st.tabs([
        "Vulnerability Report", "Suggested Fixes", "Quality Issues",
        "Human Approval", "Raw JSON", "Markdown Report",
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
        st.json(result.get("final_report_json", {}))
        st.download_button(
            "Download JSON report",
            data=json.dumps(result.get("final_report_json", {}), indent=2),
            file_name="code_review_report.json",
        )

    with tabs[5]:
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
