"""
CLI entrypoint for the AI Software Code Reviewer & Secure Development Agent.

Usage:
    python main.py --repo ./sample_repo
    python main.py --repo ./sample_repo --interactive
    python main.py --repo ./sample_repo --json-out report.json --md-out report.md

Production extensions (all additive; base behavior unchanged):
    python main.py --repo ./sample_repo --apply            # preview fixes (dry-run default)
    python main.py --repo ./sample_repo --apply --no-dry-run
    python main.py --repo ./sample_repo --repair           # self-heal unresolved findings
    python main.py --repo ./sample_repo --db reviews.db    # persist the run via Store
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

import config
from agents.approval_agent import run_interactive_cli_approval
from agents.report_agent import report_node
from graph import build_graph
from observability.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)

# Where the combined unified diff from --apply is written.
PATCH_OUTPUT_PATH = "code_fixes.patch"


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser (base flags plus additive production flags)."""
    parser = argparse.ArgumentParser(
        description="AI Software Code Reviewer & Secure Development Agent"
    )
    parser.add_argument(
        "--repo", required=True, help="Path to the repository/codebase to review"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt on the CLI for human approval of high-risk/unverified fixes",
    )
    parser.add_argument("--md-out", default="code_review_report.md")
    parser.add_argument("--json-out", default="code_review_report.json")

    # ---- production extensions ------------------------------------------
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply (or preview) suggested fixes after the review; "
        f"writes the combined diff to {PATCH_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Run the self-healing repair loop over unresolved (unverified) "
        "findings in a throwaway repo copy",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=config.APPLY_DRY_RUN_DEFAULT,
        help="With --apply, preview only (--no-dry-run to write & commit fixes). "
        f"Default: {config.APPLY_DRY_RUN_DEFAULT}",
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Persist this run (job, report, applied fixes) to a SQLite Store "
        "at PATH",
    )
    return parser


def _run_review(args: argparse.Namespace):
    """Execute the review pipeline exactly as the original CLI did."""
    app = build_graph()
    state = {
        "repo_path": args.repo,
        "auto_approve": not args.interactive,
        "errors": [],
    }

    # Interactive mode injects a CLI approval step between "approval" and the
    # final "report" node, matching the original behavior.
    if args.interactive:
        result = app.invoke(state)
        result = run_interactive_cli_approval(result)
        result = report_node(result)
    else:
        result = app.invoke(state)
    return result


def _write_reports(args: argparse.Namespace, result: Dict[str, Any]) -> None:
    """Write the markdown and JSON report artifacts to disk."""
    with open(args.md_out, "w", encoding="utf-8") as handle:
        handle.write(result["final_report_md"])
    with open(args.json_out, "w", encoding="utf-8") as handle:
        json.dump(result["final_report_json"], handle, indent=2)


def _print_summary(args: argparse.Namespace, result: Dict[str, Any]) -> None:
    """Print the standard end-of-run summary (unchanged from the original)."""
    print("\nReview complete.")
    print(f"  Quality score:     {result['quality_score']}/100")
    print(f"  Confidence score:  {result['confidence_score']}")
    print(f"  Findings:          {len(result.get('merged_findings', []))}")
    print(f"  Pending approval:  {len(result.get('requires_approval', []))}")
    print(f"  Reports written:   {args.md_out}, {args.json_out}")

    if result.get("errors"):
        print("\nNotices:")
        for note in result["errors"]:
            print(f"  - {note}")


def _do_apply(args: argparse.Namespace, result: Dict[str, Any]):
    """Apply (or preview) suggested fixes and write the combined diff.

    Returns the ``ApplyResult`` (or ``None`` if apply was not requested) so the
    caller can persist applied records.
    """
    from secure_dev.fix_applier import apply_fixes

    fixes = result.get("suggested_fixes", []) or []
    apply_result = apply_fixes(
        args.repo,
        fixes,
        dry_run=args.dry_run,
        only_verified=config.APPLY_ONLY_VERIFIED,
    )

    with open(PATCH_OUTPUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(apply_result.combined_diff)

    mode = "dry-run (no files changed)" if apply_result.dry_run else "APPLIED"
    print(f"\nFix application [{mode}]:")
    print(f"  Applied:  {len(apply_result.applied)}")
    print(f"  Skipped:  {len(apply_result.skipped)}")
    if apply_result.commit:
        print(f"  Commit:   {apply_result.commit}")
    print(f"  Diff written: {PATCH_OUTPUT_PATH}")
    for skip in apply_result.skipped:
        print(f"    - skipped {skip['file']}:{skip['line']} — {skip['reason']}")
    return apply_result


def _finding_for_fix(
    fix: Dict[str, Any], findings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Resolve the merged finding a fix addresses, synthesizing one if absent."""
    ref = fix.get("finding_ref")
    file = fix.get("file")
    line = fix.get("line")
    for finding in findings:
        if finding.get("file") == file and finding.get("rule_id") == ref:
            return finding
    for finding in findings:
        if finding.get("file") == file and finding.get("line") == line:
            return finding
    return {
        "file": file,
        "line": line,
        "rule_id": ref or "",
        "cwe": fix.get("cwe", []),
        "message": fix.get("explanation", ""),
    }


def _do_repair(args: argparse.Namespace, result: Dict[str, Any]) -> None:
    """Run the repair loop over unresolved (unverified) findings."""
    from secure_dev.repair_loop import repair_finding

    fixes = result.get("suggested_fixes", []) or []
    findings = result.get("merged_findings", []) or []
    unresolved = [fix for fix in fixes if not fix.get("verified")]

    print("\nRepair loop:")
    if not unresolved:
        print("  No unresolved findings to repair.")
        return

    resolved = 0
    for fix in unresolved:
        finding = _finding_for_fix(fix, findings)
        outcome = repair_finding(
            args.repo,
            finding,
            fix,
            max_attempts=config.MAX_REPAIR_ATTEMPTS,
        )
        resolved += 1 if outcome.resolved else 0
        status = "resolved" if outcome.resolved else "unresolved"
        target = f"{fix.get('file')}:{fix.get('line')}"
        print(f"  [{status}] {target} (attempts={outcome.attempts})")
        if outcome.notes:
            print(f"      {outcome.notes[-1]}")
    print(f"  Repaired {resolved}/{len(unresolved)} unresolved findings.")


def _persist_run(
    db_path: str, result: Dict[str, Any], apply_result: Optional[Any]
) -> None:
    """Persist the review (job, report, applied fixes) to a SQLite Store."""
    from persistence.store import Store

    store = Store(db_path)
    try:
        job_id = store.create_job(result.get("repo_path") or "")
        store.set_status(job_id, "running")
        store.save_report(job_id, result.get("final_report_json", {}))
        store.set_status(job_id, "completed")
        if apply_result is not None and not apply_result.dry_run:
            store.record_applied(job_id, apply_result.applied)
            store.set_status(job_id, "applied")
        print(f"\nPersisted run to {db_path} (job_id={job_id})")
    finally:
        store.close()


def main() -> None:
    args = _build_parser().parse_args()

    configure_logging(config.LOG_LEVEL)
    for warning in config.validate_settings():
        logger.warning("config: %s", warning)

    if not os.path.isdir(args.repo):
        raise SystemExit(f"Repo path not found: {args.repo}")

    result = _run_review(args)
    _write_reports(args, result)
    _print_summary(args, result)

    if args.repair:
        _do_repair(args, result)

    apply_result = _do_apply(args, result) if args.apply else None

    if args.db:
        _persist_run(args.db, result, apply_result)


if __name__ == "__main__":
    main()
