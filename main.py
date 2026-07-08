"""
CLI entrypoint for the AI Software Code Reviewer & Secure Development Agent.

Usage:
    python main.py --repo ./sample_repo
    python main.py --repo ./sample_repo --interactive
    python main.py --repo ./sample_repo --json-out report.json --md-out report.md
"""
from __future__ import annotations

import argparse
import json
import os

from agents.approval_agent import run_interactive_cli_approval
from agents.report_agent import report_node
from graph import build_graph


def main():
    parser = argparse.ArgumentParser(description="AI Software Code Reviewer & Secure Development Agent")
    parser.add_argument("--repo", required=True, help="Path to the repository/codebase to review")
    parser.add_argument("--interactive", action="store_true",
                         help="Prompt on the CLI for human approval of high-risk/unverified fixes")
    parser.add_argument("--md-out", default="code_review_report.md")
    parser.add_argument("--json-out", default="code_review_report.json")
    args = parser.parse_args()

    if not os.path.isdir(args.repo):
        raise SystemExit(f"Repo path not found: {args.repo}")

    app = build_graph()
    state = {
        "repo_path": args.repo,
        "auto_approve": not args.interactive,
        "errors": [],
    }

    # Invoke everything except the final report node manually if interactive,
    # so we can inject the CLI approval step between "approval" and "report".
    if args.interactive:
        # Run full graph first (approval_node will still classify items),
        # then let the user act on `requires_approval` before final report.
        result = app.invoke(state)
        result = run_interactive_cli_approval(result)
        result = report_node(result)
    else:
        result = app.invoke(state)

    with open(args.md_out, "w", encoding="utf-8") as f:
        f.write(result["final_report_md"])
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(result["final_report_json"], f, indent=2)

    print(f"\nReview complete.")
    print(f"  Quality score:     {result['quality_score']}/100")
    print(f"  Confidence score:  {result['confidence_score']}")
    print(f"  Findings:          {len(result.get('merged_findings', []))}")
    print(f"  Pending approval:  {len(result.get('requires_approval', []))}")
    print(f"  Reports written:   {args.md_out}, {args.json_out}")

    if result.get("errors"):
        print("\nNotices:")
        for e in result["errors"]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
