"""
Report Generation Agent
-------------------------
Final node. Assembles every required output artifact from the problem
statement's "Final Output" list:
  - Code Review Report
  - Bug Detection Summary
  - Security Vulnerability Report
  - Code Quality Score
  - Suggested Code Fixes
  - Secure Coding Recommendations
  - Explainability Report
  - Confidence Score
  - Human Approval Report
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from agents.state import ReviewState
from config import SCORE_WEIGHTS


def _compute_quality_score(findings: List[Dict], total_loc: int) -> float:
    if total_loc <= 0:
        total_loc = 1
    penalty = sum(SCORE_WEIGHTS.get(f.get("severity", "INFO"), 1) for f in findings)

    # Density normalization: a large repo with the same raw penalty scores
    # better because issues are sparser. Capped at 1.0 so a small repo (e.g. a
    # demo) isn't *amplified* into an unfairly harsh score.
    density_mult = min(1.0, (1000 / total_loc) ** 0.3)

    # Diminishing returns: many findings should erode the score gradually
    # rather than slamming it straight to 0. The sub-linear exponent means the
    # 20th finding hurts far less than the 1st.
    effective_penalty = (penalty ** 0.85) * density_mult

    score = max(0.0, 100.0 - effective_penalty)
    return round(score, 1)


def _severity_counts(findings: List[Dict]) -> Dict[str, int]:
    counts = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
    for f in findings:
        counts[f.get("severity", "INFO")] = counts.get(f.get("severity", "INFO"), 0) + 1
    return counts


def _render_markdown(state: ReviewState, quality_score: float, severity_counts: Dict[str, int]) -> str:
    lines = []
    lines.append(f"# AI Code Review Report")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"**Repository:** `{state.get('repo_path')}`  ")
    lines.append(f"**Files analyzed:** {len(state.get('files', []))}  ")
    lines.append(f"**Code Quality Score:** {quality_score}/100  ")
    lines.append(f"**Pipeline Confidence Score:** {state.get('confidence_score', 0)}\n")

    lines.append("## Severity Breakdown")
    for sev in ("CRITICAL", "ERROR", "WARNING", "INFO"):
        lines.append(f"- **{sev}**: {severity_counts.get(sev, 0)}")
    lines.append("")

    lines.append("## Bug Detection & Security Vulnerability Report")
    if not state.get("merged_findings"):
        lines.append("No security or bug findings detected.\n")
    for f in state.get("merged_findings", []):
        cwe = ", ".join(f.get("cwe", [])) or "n/a"
        lines.append(f"### [{f.get('severity')}] {f.get('file')}:{f.get('line')} — `{f.get('rule_id')}`")
        lines.append(f"- **CWE:** {cwe}")
        lines.append(f"- **Message:** {f.get('message')}")
        lines.append("")

    lines.append("## Code Quality Issues")
    if not state.get("quality_issues"):
        lines.append("No additional quality issues flagged.\n")
    for f in state.get("quality_issues", []):
        lines.append(f"- [{f.get('severity')}] {f.get('file')}:{f.get('line')} — {f.get('message')}")
    lines.append("")

    lines.append("## Suggested Fixes & Explainability Report")
    for fix in state.get("suggested_fixes", []):
        status = "✅ verified" if fix.get("verified") else "⚠️ flagged by verifier"
        lines.append(f"### {fix['file']}:{fix['line']} ({status})")
        lines.append(f"**Explanation:** {fix.get('explanation')}")
        lines.append(f"**Verifier notes:** {fix.get('verification_notes') or 'n/a'}")
        lines.append("**Original:**")
        lines.append(f"```\n{fix.get('original_code','')}\n```")
        lines.append("**Suggested fix:**")
        lines.append(f"```\n{fix.get('fixed_code','')}\n```")
        lines.append("")

    lines.append("## Secure Coding Recommendations (RAG-grounded)")
    seen = set()
    for guidance_list in state.get("retrieved_guidance", {}).values():
        for g in guidance_list:
            if g not in seen:
                seen.add(g)
                lines.append(f"- {g}")
    lines.append("")

    lines.append("## Human Approval Report")
    lines.append(f"- **Approved (safe to apply):** {len(state.get('approved_fixes', []))}")
    lines.append(f"- **Pending human approval:** {len(state.get('requires_approval', []))}")
    lines.append(f"- **Rejected:** {len(state.get('rejected_fixes', []))}")
    for item in state.get("requires_approval", []):
        lines.append(f"  - PENDING: {item['file']}:{item['line']} [{item['severity']}] verified={item['verified']}")
    for item in state.get("approved_fixes", []):
        lines.append(f"  - APPROVED: {item['file']}:{item['line']} [{item['severity']}]")

    if state.get("errors"):
        lines.append("\n## Pipeline Notices")
        for e in state["errors"]:
            lines.append(f"- {e}")

    return "\n".join(lines)


def report_node(state: ReviewState) -> ReviewState:
    total_loc = state.get("code_graph_summary", {}).get("total_loc", 0)
    all_findings = state.get("merged_findings", []) + state.get("quality_issues", [])
    quality_score = _compute_quality_score(all_findings, total_loc)
    severity_counts = _severity_counts(state.get("merged_findings", []))

    report_json = {
        "repo_path": state.get("repo_path"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files_analyzed": len(state.get("files", [])),
        "quality_score": quality_score,
        "confidence_score": state.get("confidence_score", 0),
        "severity_breakdown": severity_counts,
        "bug_and_vulnerability_findings": state.get("merged_findings", []),
        "quality_issues": state.get("quality_issues", []),
        "suggested_fixes": state.get("suggested_fixes", []),
        "review_comments": state.get("review_comments", []),
        "verifier_report": state.get("verifier_report", {}),
        "human_approval": {
            "approved": state.get("approved_fixes", []),
            "pending": state.get("requires_approval", []),
            "rejected": state.get("rejected_fixes", []),
        },
        "notices": state.get("errors", []),
    }

    md = _render_markdown(state, quality_score, severity_counts)

    return {
        **state,
        "quality_score": quality_score,
        "final_report_json": report_json,
        "final_report_md": md,
    }
