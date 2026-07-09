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
    """Classify every suggested fix into auto-approved vs. needs-human, and
    attach a plain-language reason to each so the UI can explain the decision.

    Policy (auto_approve=True): a fix is auto-approved ONLY if it is both
    Verifier-confirmed AND below the high-severity threshold. High-severity
    (ERROR/CRITICAL) or Verifier-flagged fixes are escalated for human sign-off
    — high-risk changes are never silently auto-applied. With auto_approve=False,
    EVERY fix is routed to the human queue.
    """
    fixes = state.get("suggested_fixes", [])
    severity_lookup = _severity_lookup(state)
    auto_approve = state.get("auto_approve", False)

    requires_approval: List[Dict] = []
    approved: List[Dict] = []
    rejected: List[Dict] = []

    for fix in fixes:
        severity = severity_lookup.get(fix.get("finding_ref", ""), "WARNING")
        verified = fix.get("verified", False)
        high_risk = severity in HUMAN_APPROVAL_SEVERITIES

        item = {
            "finding_ref": fix.get("finding_ref", ""),
            "file": fix.get("file", "?"),
            "line": fix.get("line", 0),
            "severity": severity,
            "verified": verified,
            "explanation": fix.get("explanation", ""),
        }

        # Decide destination + reason.
        if not auto_approve:
            item["auto_approved"] = False
            item["approval_reason"] = ("Manual approval mode — every fix needs a "
                                       "human decision.")
            requires_approval.append(item)
        elif high_risk:
            item["auto_approved"] = False
            item["approval_reason"] = (f"High severity ({severity}) — escalated for "
                                       f"human sign-off before it can be applied.")
            requires_approval.append(item)
        elif not verified:
            item["auto_approved"] = False
            item["approval_reason"] = ("Verifier could not confirm this fix "
                                       "(possible hallucination/no-op) — needs a "
                                       "human to review.")
            requires_approval.append(item)
        else:
            item["auto_approved"] = True
            item["approval_reason"] = (f"{severity} severity and Verifier-confirmed "
                                       f"— low risk, auto-approved.")
            approved.append(item)

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
