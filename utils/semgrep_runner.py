"""
Wraps the `semgrep` CLI (static & semantic code analysis, referenced
directly in the problem statement's Tools & Frameworks list) and normalizes
its JSON output into our FileFinding schema.

If semgrep is not installed in the environment, this module degrades
gracefully to an empty findings list plus a warning in `errors`, so the
rest of the pipeline (LLM-based review, RAG) still runs end-to-end.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Dict, List, Tuple

from config import SEMGREP_RULESETS

_SEVERITY_MAP = {"INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR"}

_LOCAL_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rules", "local_security_rules.yaml")


def is_semgrep_available() -> bool:
    return shutil.which("semgrep") is not None


def _run_semgrep_cmd(repo_path: str, configs: List[str], timeout: int):
    cmd = ["semgrep", "--json", "--no-git-ignore", "--metrics=off"]
    for cfg in configs:
        cmd += ["--config", cfg]
    cmd.append(repo_path)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def run_semgrep(repo_path: str, timeout: int = 300) -> Tuple[List[Dict], List[str]]:
    """Run configured semgrep rulesets against a repo path.

    Tries the hosted Semgrep Registry rulesets first (broad CWE/OWASP
    coverage). If those can't be fetched (offline judging environment,
    firewalled network, etc.) it transparently falls back to the bundled
    offline ruleset at rules/local_security_rules.yaml so static analysis
    still runs end-to-end.

    Returns (findings, errors)
    """
    errors: List[str] = []
    if not is_semgrep_available():
        errors.append("semgrep binary not found on PATH — skipping static analysis. "
                       "Install with `pip install semgrep`.")
        return [], errors

    try:
        proc = _run_semgrep_cmd(repo_path, SEMGREP_RULESETS, timeout)
    except subprocess.TimeoutExpired:
        errors.append(f"semgrep timed out after {timeout}s")
        return [], errors
    except FileNotFoundError:
        errors.append("semgrep executable missing")
        return [], errors

    registry_failed = proc.returncode not in (0, 1) or "invalid configuration file" in (proc.stderr or "")
    if registry_failed:
        errors.append(
            "Could not fetch Semgrep Registry rulesets (offline/firewalled environment?) — "
            "falling back to bundled offline ruleset at rules/local_security_rules.yaml."
        )
        try:
            proc = _run_semgrep_cmd(repo_path, [_LOCAL_RULES_PATH], timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            errors.append(f"local ruleset scan failed: {e}")
            return [], errors

    if proc.returncode not in (0, 1):  # 1 = findings present, still valid
        errors.append(f"semgrep exited {proc.returncode}: {proc.stderr[:500]}")

    findings: List[Dict] = []
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        errors.append("could not parse semgrep JSON output")
        return [], errors

    for result in data.get("results", []):
        extra = result.get("extra", {})
        metadata = extra.get("metadata", {})
        severity = _SEVERITY_MAP.get(extra.get("severity", "WARNING"), "WARNING")
        cwe = metadata.get("cwe", [])
        if isinstance(cwe, str):
            cwe = [cwe]
        owasp = metadata.get("owasp", [])
        if isinstance(owasp, str):
            owasp = [owasp]

        rule_id = result.get("check_id", "unknown-rule")
        if "." in rule_id and not rule_id.startswith("python-"):
            rule_id = rule_id.rsplit(".", 1)[-1]  # strip local-file path prefix, e.g. "rules.foo" -> "foo"

        findings.append({
            "file": result.get("path", ""),
            "line": result.get("start", {}).get("line", 0),
            "end_line": result.get("end", {}).get("line", 0),
            "rule_id": rule_id,
            "cwe": cwe,
            "owasp": owasp,
            "severity": severity,
            "message": extra.get("message", "").strip(),
            "source": "semgrep",
            "code_snippet": extra.get("lines", "").strip(),
        })

    for err in data.get("errors", []):
        errors.append(str(err.get("message", err))[:300])

    return findings, errors
