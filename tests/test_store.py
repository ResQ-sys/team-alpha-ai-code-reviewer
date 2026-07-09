"""Unit tests for :class:`persistence.store.Store`.

All tests use in-memory or ``tmp_path`` SQLite databases; nothing touches the
real repo, network, or an LLM.
"""

from __future__ import annotations

import os
import threading

import pytest

from persistence.store import STATUS_PENDING, Store


@pytest.fixture()
def store() -> Store:
    """An in-memory store, closed after the test."""
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


# -- creation / round-trip ------------------------------------------------
def test_create_job_returns_uuid_and_pending_status(store: Store) -> None:
    job_id = store.create_job("/repo/alpha")

    assert isinstance(job_id, str) and len(job_id) == 36  # uuid4 canonical form
    job = store.get_job(job_id)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["repo_path"] == "/repo/alpha"
    assert job["status"] == STATUS_PENDING
    assert job["error"] is None
    assert job["report"] is None
    assert job["decisions"] is None
    assert job["applied"] is None
    assert job["created_at"] and job["updated_at"]


def test_create_job_generates_unique_ids(store: Store) -> None:
    ids = {store.create_job("/repo") for _ in range(25)}
    assert len(ids) == 25


def test_create_job_rejects_empty_repo_path(store: Store) -> None:
    with pytest.raises(ValueError):
        store.create_job("")


def test_get_job_missing_returns_none(store: Store) -> None:
    assert store.get_job("does-not-exist") is None


# -- status transitions ----------------------------------------------------
def test_set_status_transitions(store: Store) -> None:
    job_id = store.create_job("/repo")

    for status in ("running", "completed", "approved", "applied"):
        store.set_status(job_id, status)
        assert store.get_job(job_id)["status"] == status


def test_set_status_error_records_message(store: Store) -> None:
    job_id = store.create_job("/repo")
    store.set_status(job_id, "error", error="semgrep exploded")

    job = store.get_job(job_id)
    assert job["status"] == "error"
    assert job["error"] == "semgrep exploded"


def test_set_status_rejects_unknown_status(store: Store) -> None:
    job_id = store.create_job("/repo")
    with pytest.raises(ValueError):
        store.set_status(job_id, "bogus")


def test_set_status_missing_job_raises_keyerror(store: Store) -> None:
    with pytest.raises(KeyError):
        store.set_status("nope", "running")


def test_set_status_updates_timestamp(store: Store) -> None:
    job_id = store.create_job("/repo")
    before = store.get_job(job_id)["updated_at"]
    store.set_status(job_id, "running")
    after = store.get_job(job_id)["updated_at"]
    assert after >= before


# -- reports ---------------------------------------------------------------
def test_save_report_round_trip(store: Store) -> None:
    job_id = store.create_job("/repo")
    report = {
        "repo_path": "/repo",
        "quality_score": 82,
        "severity_breakdown": {"CRITICAL": 1, "ERROR": 2},
        "suggested_fixes": [
            {"file": "a.py", "line": 10, "rule_id": "sqli", "verified": True}
        ],
    }
    store.save_report(job_id, report)

    got = store.get_job(job_id)["report"]
    assert got == report
    assert got["suggested_fixes"][0]["verified"] is True


def test_save_report_missing_job_raises_keyerror(store: Store) -> None:
    with pytest.raises(KeyError):
        store.save_report("nope", {"quality_score": 1})


# -- approvals & applied ---------------------------------------------------
def test_record_approval_round_trip(store: Store) -> None:
    job_id = store.create_job("/repo")
    decisions = {"a.py:10:sqli": "approved", "b.py:3:eval": "rejected"}
    store.record_approval(job_id, decisions)

    assert store.get_job(job_id)["decisions"] == decisions


def test_record_applied_round_trip(store: Store) -> None:
    job_id = store.create_job("/repo")
    applied = [
        {"file": "a.py", "line": 10, "applied": True, "diff": "--- a\n+++ b"},
        {"file": "b.py", "line": 3, "applied": False, "error": "no match"},
    ]
    store.record_applied(job_id, applied)

    got = store.get_job(job_id)["applied"]
    assert got == applied
    assert got[1]["applied"] is False


def test_record_approval_missing_job_raises_keyerror(store: Store) -> None:
    with pytest.raises(KeyError):
        store.record_approval("nope", {})


# -- listing ---------------------------------------------------------------
def test_list_jobs_returns_recent_first(store: Store) -> None:
    ids = [store.create_job(f"/repo/{i}") for i in range(5)]
    listed = store.list_jobs()

    assert len(listed) == 5
    listed_ids = [j["job_id"] for j in listed]
    # Most recently created should appear first.
    assert listed_ids[0] == ids[-1]
    assert set(listed_ids) == set(ids)


def test_list_jobs_respects_limit(store: Store) -> None:
    for i in range(10):
        store.create_job(f"/repo/{i}")
    assert len(store.list_jobs(limit=3)) == 3


def test_list_jobs_empty_store(store: Store) -> None:
    assert store.list_jobs() == []


# -- persistence to disk & reopen -----------------------------------------
def test_persists_to_disk_across_reopen(tmp_path) -> None:
    db_path = os.path.join(str(tmp_path), "reviews.db")

    s1 = Store(db_path)
    job_id = s1.create_job("/repo/persist")
    s1.save_report(job_id, {"quality_score": 90})
    s1.close()

    s2 = Store(db_path)
    try:
        job = s2.get_job(job_id)
        assert job is not None
        assert job["report"] == {"quality_score": 90}
    finally:
        s2.close()


# -- thread safety ---------------------------------------------------------
def test_concurrent_create_job_is_thread_safe(store: Store) -> None:
    created: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(20):
            jid = store.create_job("/repo/concurrent")
            with lock:
                created.append(jid)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(created) == 160
    assert len(set(created)) == 160  # no id collisions / lost writes
    assert len(store.list_jobs(limit=1000)) == 160


# -- context manager -------------------------------------------------------
def test_store_context_manager_closes() -> None:
    with Store(":memory:") as s:
        jid = s.create_job("/repo/ctx")
        assert s.get_job(jid) is not None
