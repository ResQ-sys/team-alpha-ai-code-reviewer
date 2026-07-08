"""
Human-in-the-Loop Approval Agent
----------------------------------
Any fix touching a HIGH/CRITICAL-severity finding — or any fix the Verifier
flagged as unverified — is routed for explicit human approval before it is
considered "applyable" to the repository. Covers "Human-in-the-Loop
Approval" and "Escalate high-risk security issues or production-level code
changes for human approval" from the architecture and Requirements.

Two modes:
  - interactive (auto_approve=False): prompts on the CLI for each item
    needing approval (yes/no/skip). Used by main.py.
  - auto_approve=True: everything verified + below the severity threshold
    is auto-approved; everything else is left pending for a reviewer to
    action later (e.g. via the Streamlit dashboard) — nothing is silently
    auto-applied for high-risk changes even in auto mode.
"""
from __future__ import annotations

from typing import Dict, List

from agents.state import ReviewState
from config import HUMAN_APPROVAL_SEVERITIES

_severity_by_ref_cache_key = "_severity_by_ref"


def _severity_lookup(state: ReviewState) -> Dict[str, str]:
    lookup = {}
    for f in state.get("merged_findings", []):
        ref = f"{f['file']}:{f.get('line', 0)}:{f.get('rule_id', '')}"
        lookup[ref] = f.get("severity", "WARNING")
    return lookup


def approval_node(state: ReviewState) -> ReviewState:
    fixes = state.get("suggested_fixes", [])
    severity_lookup = _severity_lookup(state)
    auto_approve = state.get("auto_approve", False)

    requires_approval: List[Dict] = []
    approved: List[Dict] = []
    rejected: List[Dict] = []

    for fix in fixes:
        severity = severity_lookup.get(fix["finding_ref"], "WARNING")
        needs_review = severity in HUMAN_APPROVAL_SEVERITIES or not fix.get("verified", False)

        item = {
            "finding_ref": fix["finding_ref"],
            "file": fix["file"],
            "line": fix["line"],
            "severity": severity,
            "verified": fix.get("verified", False),
            "explanation": fix.get("explanation", ""),
        }

        if not needs_review:
            approved.append(item)
            continue

        if auto_approve and fix.get("verified", False):
            # Even in auto mode, unverified or high-severity items are NOT
            # silently applied — only verified + non-critical items can
            # skip the queue, matching the "never auto-apply high risk"
            # requirement.
            if severity not in HUMAN_APPROVAL_SEVERITIES:
                approved.append(item)
                continue

        requires_approval.append(item)

    return {
        **state,
        "requires_approval": requires_approval,
        "approved_fixes": approved,
        "rejected_fixes": rejected,
    }


def run_interactive_cli_approval(state: ReviewState) -> ReviewState:
    """Optional CLI helper: walks `requires_approval` items and prompts the
    user. Call this from main.py in --interactive mode, after approval_node
    has already classified which items truly need a human decision."""
    approved = list(state.get("approved_fixes", []))
    rejected = list(state.get("rejected_fixes", []))
    pending = list(state.get("requires_approval", []))
    still_pending = []

    print(f"\n{len(pending)} fix(es) require human approval "
          f"(high severity and/or unverified by the Verifier Agent):\n")

    for item in pending:
        print(f"--- {item['file']}:{item['line']} [{item['severity']}] "
              f"verified={item['verified']} ---")
        print(f"Reasoning: {item['explanation'][:300]}")
        choice = input("Approve this fix? [y/n/s(kip for now)]: ").strip().lower()
        if choice == "y":
            approved.append(item)
        elif choice == "n":
            rejected.append(item)
        else:
            still_pending.append(item)

    return {
        **state,
        "approved_fixes": approved,
        "rejected_fixes": rejected,
        "requires_approval": still_pending,
    }
