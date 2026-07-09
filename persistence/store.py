"""Thread-safe SQLite job store for the AI code-review pipeline.

The :class:`Store` persists review *jobs* and everything derived from them:
the generated report, human-approval decisions, and applied-fix records.
Rich payloads (report / decisions / applied) are stored as JSON text columns
so the schema stays stable regardless of report shape.

Design notes
------------
* A single :class:`sqlite3.Connection` is opened with
  ``check_same_thread=False`` and every mutation/read is guarded by a
  re-entrant lock, making the store safe to share across the FastAPI
  background threads that drive the pipeline.
* ``db_path=":memory:"`` is fully supported for tests. Because a shared
  connection is used, the in-memory database persists for the lifetime of
  the ``Store`` instance.
* All timestamps are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

__all__ = ["Store"]

# Job lifecycle states used across the pipeline / API.
STATUS_PENDING = "pending"
VALID_STATUSES = {
    STATUS_PENDING,
    "running",
    "completed",
    "approved",
    "applied",
    "error",
}


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> Optional[str]:
    """Serialise ``value`` to JSON text, or ``None`` when ``value`` is ``None``."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _loads(text: Optional[str]) -> Any:
    """Deserialise JSON ``text`` produced by :func:`_dumps`; ``None`` passes through."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        # Corrupt/legacy value: surface the raw text rather than crashing a read.
        return text


class Store:
    """SQLite-backed, thread-safe persistence for review jobs."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """Open (or create) the database at ``db_path`` and ensure the schema.

        Args:
            db_path: Filesystem path to the SQLite file, or ``":memory:"``.
        """
        self._db_path = db_path
        self._lock = threading.RLock()
        # A single shared connection keeps :memory: databases alive and gives
        # us a natural serialization point together with ``self._lock``.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._ensure_schema()

    # -- schema ------------------------------------------------------------
    def _ensure_schema(self) -> None:
        """Create the ``jobs`` table if it does not already exist."""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id     TEXT PRIMARY KEY,
                    repo_path  TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    error      TEXT,
                    report     TEXT,
                    decisions  TEXT,
                    applied    TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    # -- writes ------------------------------------------------------------
    def create_job(self, repo_path: str) -> str:
        """Insert a new job row in ``pending`` status and return its id.

        Args:
            repo_path: The repository path to be analysed.

        Returns:
            The generated UUID job id.
        """
        if not repo_path:
            raise ValueError("repo_path must be a non-empty string")
        job_id = str(uuid.uuid4())
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs
                    (job_id, repo_path, status, error, report,
                     decisions, applied, created_at, updated_at)
                VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (job_id, repo_path, STATUS_PENDING, now, now),
            )
            self._conn.commit()
        return job_id

    def set_status(
        self, job_id: str, status: str, error: Optional[str] = None
    ) -> None:
        """Update a job's ``status`` (and optional ``error`` message).

        Args:
            job_id: Target job id.
            status: New status; must be one of :data:`VALID_STATUSES`.
            error: Optional error detail (stored verbatim).

        Raises:
            ValueError: If ``status`` is unknown.
            KeyError: If ``job_id`` does not exist.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {sorted(VALID_STATUSES)}"
            )
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE job_id = ?",
                (status, error, _utc_now(), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no such job: {job_id}")
            self._conn.commit()

    def save_report(self, job_id: str, report: dict) -> None:
        """Persist the final report JSON for a job.

        Args:
            job_id: Target job id.
            report: The report dictionary produced by ``report_agent``.

        Raises:
            KeyError: If ``job_id`` does not exist.
        """
        self._update_json_column(job_id, "report", report)

    def record_approval(self, job_id: str, decisions: dict) -> None:
        """Persist human-approval decisions for a job.

        Args:
            job_id: Target job id.
            decisions: Mapping of fix keys to approval decisions.

        Raises:
            KeyError: If ``job_id`` does not exist.
        """
        self._update_json_column(job_id, "decisions", decisions)

    def record_applied(self, job_id: str, applied: list[dict]) -> None:
        """Persist the list of applied-fix records for a job.

        Args:
            job_id: Target job id.
            applied: List of applied-fix dicts (from ``fix_applier``).

        Raises:
            KeyError: If ``job_id`` does not exist.
        """
        self._update_json_column(job_id, "applied", applied)

    def _update_json_column(self, job_id: str, column: str, value: Any) -> None:
        """Serialise ``value`` into ``column`` for ``job_id`` and bump ``updated_at``.

        ``column`` is a fixed internal identifier (never user input), so the
        interpolation below is safe from SQL injection.
        """
        assert column in {"report", "decisions", "applied"}
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE jobs SET {column} = ?, updated_at = ? WHERE job_id = ?",
                (_dumps(value), _utc_now(), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"no such job: {job_id}")
            self._conn.commit()

    # -- reads -------------------------------------------------------------
    def get_job(self, job_id: str) -> Optional[dict]:
        """Return the full job record, or ``None`` if it does not exist.

        Args:
            job_id: Target job id.

        Returns:
            A dict with keys ``job_id, repo_path, status, error, report,
            decisions, applied, created_at, updated_at`` (JSON columns
            deserialised), or ``None``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_jobs(self, limit: int = 50) -> list[dict]:
        """Return recent jobs, most-recently-created first.

        Args:
            limit: Maximum number of rows to return (clamped to >= 0).

        Returns:
            A list of job dicts (same shape as :meth:`get_job`).
        """
        limit = max(0, int(limit))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a raw ``sqlite3.Row`` into a dict, decoding JSON columns."""
        return {
            "job_id": row["job_id"],
            "repo_path": row["repo_path"],
            "status": row["status"],
            "error": row["error"],
            "report": _loads(row["report"]),
            "decisions": _loads(row["decisions"]),
            "applied": _loads(row["applied"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
