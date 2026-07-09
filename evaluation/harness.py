"""Offline CWE-detection evaluation harness.

Scores the pipeline's ability to detect known-vulnerable code by running static
analysis (and, optionally, the full LangGraph pipeline) over a directory of
fixtures. Each fixture is a folder under ``fixtures/`` containing one or more
vulnerable source files plus an ``expected.json`` manifest::

    {"expected_cwes": ["CWE-89", "CWE-78", ...]}

Detection metrics are computed as precision / recall / F1 over the set of CWE
identifiers, both per-fixture and aggregated (micro-average).

Design goals:
  * **Offline-first.** With only ``semgrep`` installed the harness runs fully
    offline and deterministically. If semgrep is missing it degrades to empty
    detections (recall 0) rather than crashing.
  * **Mockable.** ``run_semgrep`` / ``is_semgrep_available`` are imported into
    this module's namespace so tests can monkeypatch them, and an optional
    dependency-injected ``llm`` supplements detection without a live model.

Public API:
    run_eval(fixtures_dir=None, *, use_pipeline=True, llm=None, out_dir=None) -> dict
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.semgrep_runner import is_semgrep_available, run_semgrep

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_FIXTURES_DIR = os.path.join(_PACKAGE_DIR, "fixtures")
_EXPECTED_MANIFEST = "expected.json"
_SEMGREP_TIMEOUT = 120
_CWE_RE = re.compile(r"CWE[-_\s]?(\d+)", re.IGNORECASE)

# JSON-schema-ish spec used when an LLM detector is injected.
_LLM_CWE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["cwes"],
    "properties": {"cwes": {"type": "array", "items": {"type": "string"}}},
}
_LLM_SYSTEM = (
    "You are a security static-analysis assistant. Identify CWE identifiers "
    "for vulnerabilities present in the given source file."
)


# ---------------------------------------------------------------------------
# CWE normalization
# ---------------------------------------------------------------------------

def normalize_cwe(raw: Any) -> Optional[str]:
    """Canonicalize a CWE reference to ``"CWE-<n>"`` or return ``None``.

    Accepts values like ``"CWE-89"``, ``"cwe_89"``, ``"CWE-89: SQL Injection"``
    or the bare integer ``89``. Anything without a CWE number yields ``None``.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Bare integer or all-digit string, e.g. 89 / "89".
    if text.isdigit():
        return f"CWE-{int(text)}"
    match = _CWE_RE.search(text)
    if not match:
        return None
    return f"CWE-{int(match.group(1))}"


def _cwes_from_findings(findings: List[Dict[str, Any]]) -> Set[str]:
    """Collect the normalized CWE set referenced by a list of findings."""
    detected: Set[str] = set()
    for finding in findings or []:
        cwe_field = finding.get("cwe", [])
        if isinstance(cwe_field, str):
            cwe_field = [cwe_field]
        for entry in cwe_field or []:
            norm = normalize_cwe(entry)
            if norm:
                detected.add(norm)
    return detected


# ---------------------------------------------------------------------------
# Precision / recall / F1
# ---------------------------------------------------------------------------

def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    """Compute precision, recall and F1 from confusion counts (safe on zeros)."""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _score(expected: Set[str], detected: Set[str]) -> Dict[str, Any]:
    """Score one detected set against the expected set for a fixture."""
    true_positives = sorted(expected & detected)
    false_positives = sorted(detected - expected)
    false_negatives = sorted(expected - detected)
    metrics = _prf(len(true_positives), len(false_positives), len(false_negatives))
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        **metrics,
    }


# ---------------------------------------------------------------------------
# Fixture discovery / loading
# ---------------------------------------------------------------------------

def _discover_fixtures(fixtures_dir: str) -> List[str]:
    """Return sorted absolute paths of fixture dirs (those with a manifest)."""
    if not os.path.isdir(fixtures_dir):
        return []
    result: List[str] = []
    for name in sorted(os.listdir(fixtures_dir)):
        path = os.path.join(fixtures_dir, name)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, _EXPECTED_MANIFEST)):
            result.append(path)
    return result


def _load_expected(fixture_dir: str) -> Set[str]:
    """Read and validate a fixture's expected CWE set from its manifest."""
    manifest_path = os.path.join(fixture_dir, _EXPECTED_MANIFEST)
    with open(manifest_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "expected_cwes" not in data:
        raise ValueError(f"{manifest_path}: manifest must contain 'expected_cwes'")
    raw = data["expected_cwes"]
    if not isinstance(raw, list):
        raise ValueError(f"{manifest_path}: 'expected_cwes' must be a list")
    expected: Set[str] = set()
    for entry in raw:
        norm = normalize_cwe(entry)
        if norm is None:
            raise ValueError(f"{manifest_path}: invalid CWE reference {entry!r}")
        expected.add(norm)
    return expected


# ---------------------------------------------------------------------------
# Detection paths
# ---------------------------------------------------------------------------

def _detect_static(fixture_dir: str, notes: List[str]) -> Set[str]:
    """Run semgrep static analysis over a fixture; empty set if unavailable."""
    if not is_semgrep_available():
        notes.append("semgrep unavailable — static analysis skipped")
        return set()
    findings, errors = run_semgrep(fixture_dir, timeout=_SEMGREP_TIMEOUT)
    notes.extend(errors)
    return _cwes_from_findings(findings)


def _detect_pipeline(fixture_dir: str, notes: List[str], fix_stats: Dict[str, int]) -> Set[str]:
    """Run the full pipeline over a fixture; degrade gracefully on failure."""
    try:
        from graph import run_pipeline  # lazy: avoids heavy import when unused
    except Exception as exc:  # noqa: BLE001 - importing the graph is best-effort
        notes.append(f"pipeline unavailable: {exc}")
        return set()
    try:
        result = run_pipeline(fixture_dir, auto_approve=True)
    except Exception as exc:  # noqa: BLE001 - never let one fixture abort the run
        notes.append(f"pipeline failed: {exc}")
        return set()
    report = result.get("final_report_json", {}) or {}
    findings = report.get("bug_and_vulnerability_findings", [])
    for fix in report.get("suggested_fixes", []) or []:
        if fix.get("fixed_code"):
            fix_stats["total"] += 1
            if fix.get("verified"):
                fix_stats["verified"] += 1
    notes.extend(report.get("notices", []) or [])
    return _cwes_from_findings(findings)


def _detect_llm(fixture_dir: str, llm: Any, notes: List[str]) -> Set[str]:
    """Supplement detection with an injected LLM (mockable in tests)."""
    detected: Set[str] = set()
    for fname in sorted(os.listdir(fixture_dir)):
        if not fname.endswith(".py"):
            continue
        try:
            with open(os.path.join(fixture_dir, fname), "r", encoding="utf-8") as handle:
                source = handle.read()
            response = llm.complete_schema(
                _LLM_SYSTEM,
                f"Source file `{fname}`:\n```\n{source[:8000]}\n```\n"
                "Return JSON {\"cwes\": [\"CWE-<n>\", ...]}.",
                _LLM_CWE_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001 - LLM is optional; keep going
            notes.append(f"llm detection failed for {fname}: {exc}")
            continue
        if isinstance(response, dict) and not response.get("_parse_error"):
            for entry in response.get("cwes", []) or []:
                norm = normalize_cwe(entry)
                if norm:
                    detected.add(norm)
    return detected


# ---------------------------------------------------------------------------
# Per-fixture evaluation
# ---------------------------------------------------------------------------

def _evaluate_fixture(
    fixture_dir: str,
    *,
    use_pipeline: bool,
    llm: Any,
    fix_stats: Dict[str, int],
) -> Dict[str, Any]:
    """Evaluate a single fixture directory and return its scored result."""
    name = os.path.basename(fixture_dir.rstrip(os.sep))
    notes: List[str] = []
    expected = _load_expected(fixture_dir)

    detected: Set[str] = _detect_static(fixture_dir, notes)
    if use_pipeline:
        detected |= _detect_pipeline(fixture_dir, notes, fix_stats)
    if llm is not None:
        detected |= _detect_llm(fixture_dir, llm, notes)

    scored = _score(expected, detected)
    return {
        "name": name,
        "expected_cwes": sorted(expected),
        "detected_cwes": sorted(detected),
        "notes": notes,
        **scored,
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_markdown(report: Dict[str, Any]) -> str:
    """Render a human-readable Markdown summary of an eval report."""
    agg = report["aggregate"]
    lines = [
        "# CWE Detection Evaluation Report",
        f"_Generated {report['generated_at']}_",
        "",
        f"- **Fixtures evaluated:** {report['num_fixtures']}",
        f"- **Semgrep available:** {report['semgrep_available']}",
        f"- **Pipeline detection:** {report['use_pipeline']}",
        "",
        "## Aggregate (micro-average)",
        f"- **Precision:** {agg['precision']}",
        f"- **Recall:** {agg['recall']}",
        f"- **F1:** {agg['f1']}",
        f"- **TP / FP / FN:** {agg['tp']} / {agg['fp']} / {agg['fn']}",
        f"- **Fix success:** {agg['fix_success']}",
        "",
        "## Per-fixture",
        "",
        "| Fixture | Expected | Detected | P | R | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["per_fixture"]:
        lines.append(
            f"| {item['name']} | {', '.join(item['expected_cwes']) or '-'} "
            f"| {', '.join(item['detected_cwes']) or '-'} "
            f"| {item['precision']} | {item['recall']} | {item['f1']} |"
        )
    return "\n".join(lines) + "\n"


def _write_reports(report: Dict[str, Any], out_dir: str) -> None:
    """Persist ``report.json`` and ``report.md`` into ``out_dir``."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "report.json")
    md_path = os.path.join(out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(_render_markdown(report))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_eval(
    fixtures_dir: Optional[str] = None,
    *,
    use_pipeline: bool = True,
    llm: Any = None,
    out_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate CWE detection over fixtures and write ``report.json`` / ``.md``.

    Args:
        fixtures_dir: Directory of fixture subdirs. Defaults to the packaged
            ``evaluation/fixtures``.
        use_pipeline: If True, also run the full pipeline for detection and to
            gather a fix-success rate. Static analysis always runs.
        llm: Optional object with ``complete_schema(system, prompt, schema)``
            used as a supplemental detector (dependency-injected / mockable).
        out_dir: Where to write reports. Defaults to the ``evaluation`` package
            directory. Tests pass a tmp dir to avoid mutating the repo.

    Returns:
        The full report dict (also written to disk).
    """
    fixtures_dir = fixtures_dir or _DEFAULT_FIXTURES_DIR
    out_dir = out_dir or _PACKAGE_DIR

    fixture_dirs = _discover_fixtures(fixtures_dir)
    fix_stats: Dict[str, int] = {"total": 0, "verified": 0}
    per_fixture: List[Dict[str, Any]] = []
    tp = fp = fn = 0

    for fixture_dir in fixture_dirs:
        item = _evaluate_fixture(
            fixture_dir, use_pipeline=use_pipeline, llm=llm, fix_stats=fix_stats
        )
        per_fixture.append(item)
        tp += len(item["true_positives"])
        fp += len(item["false_positives"])
        fn += len(item["false_negatives"])

    aggregate: Dict[str, Any] = {"tp": tp, "fp": fp, "fn": fn, **_prf(tp, fp, fn)}
    aggregate["fix_success"] = (
        round(fix_stats["verified"] / fix_stats["total"], 4)
        if fix_stats["total"]
        else None
    )

    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixtures_dir": fixtures_dir,
        "num_fixtures": len(fixture_dirs),
        "semgrep_available": is_semgrep_available(),
        "use_pipeline": use_pipeline,
        "per_fixture": per_fixture,
        "aggregate": aggregate,
    }

    _write_reports(report, out_dir)
    return report


def _main() -> int:
    """CLI entry: run an OFFLINE (semgrep-only) evaluation and print a summary."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the CWE-detection eval harness.")
    parser.add_argument("--fixtures", default=None, help="fixtures directory")
    parser.add_argument("--out", default=None, help="output directory for reports")
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="also run the full pipeline (needs an LLM backend; slower)",
    )
    args = parser.parse_args()

    report = run_eval(
        args.fixtures, use_pipeline=args.pipeline, out_dir=args.out
    )
    agg = report["aggregate"]
    print(
        f"Fixtures: {report['num_fixtures']} | semgrep={report['semgrep_available']} | "
        f"P={agg['precision']} R={agg['recall']} F1={agg['f1']} "
        f"(TP={agg['tp']} FP={agg['fp']} FN={agg['fn']})"
    )
    out = args.out or _PACKAGE_DIR
    print(f"Reports written to {os.path.join(out, 'report.json')} and report.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
