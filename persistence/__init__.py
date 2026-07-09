"""Persistence layer for the AI code-review pipeline.

Exposes :class:`Store`, a thread-safe SQLite-backed job store that records
review jobs, their status transitions, generated reports, human approval
decisions, and applied-fix records.
"""

from persistence.store import Store

__all__ = ["Store"]
