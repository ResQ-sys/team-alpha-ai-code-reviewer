"""Offline evaluation harness for the AI code-review pipeline.

Exposes :func:`evaluation.harness.run_eval`, which scores CWE detection over a
set of vulnerable fixtures and writes ``report.json`` / ``report.md``.
"""
from __future__ import annotations

__all__ = ["run_eval"]


def __getattr__(name: str):  # pragma: no cover - thin lazy re-export
    if name == "run_eval":
        from evaluation.harness import run_eval

        return run_eval
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
