"""Tests for the live agent-activity event plumbing.

Two layers are covered:

* :class:`observability.events.EventBus` — publish / subscribe / replay / close /
  bounded-buffer / singleton semantics, exercised on fresh bus instances so the
  process-wide singleton is never polluted.
* ``GET /api/jobs/{id}/events`` — a fake pipeline publishes a handful of agent
  events onto the *shared* bus (keyed by the job's UUID); the SSE endpoint must
  stream them back in order and terminate with ``[DONE]``.
"""

from __future__ import annotations

import json
import queue
import time

import pytest
from fastapi.testclient import TestClient

import graph
from observability.events import CLOSE, EventBus, get_bus
from persistence.store import Store
from server.api import create_app


# ---------------------------------------------------------------------------
# EventBus unit tests
# ---------------------------------------------------------------------------
def test_publish_then_subscriber_receives_subsequent_events() -> None:
    bus = EventBus()
    sub = bus.subscribe("job1")
    bus.publish("job1", {"type": "agent", "node": "ingestion", "status": "running"})
    assert sub.get_nowait() == {"type": "agent", "node": "ingestion", "status": "running"}


def test_replay_delivers_events_published_before_subscribe() -> None:
    bus = EventBus()
    bus.publish("job1", {"seq": 1})
    bus.publish("job1", {"seq": 2})
    sub = bus.subscribe("job1")  # attaches late — must still see history.
    assert sub.get_nowait() == {"seq": 1}
    assert sub.get_nowait() == {"seq": 2}


def test_close_pushes_sentinel_to_live_subscribers() -> None:
    bus = EventBus()
    sub = bus.subscribe("job1")
    bus.publish("job1", {"seq": 1})
    bus.close("job1")
    assert sub.get_nowait() == {"seq": 1}
    assert sub.get_nowait() is CLOSE


def test_subscribe_after_close_replays_then_sentinel() -> None:
    bus = EventBus()
    bus.publish("job1", {"seq": 1})
    bus.close("job1")
    sub = bus.subscribe("job1")  # job already finished.
    assert sub.get_nowait() == {"seq": 1}
    assert sub.get_nowait() is CLOSE


def test_multiple_subscribers_each_receive_every_event() -> None:
    bus = EventBus()
    a = bus.subscribe("job1")
    b = bus.subscribe("job1")
    bus.publish("job1", {"seq": 1})
    assert a.get_nowait() == {"seq": 1}
    assert b.get_nowait() == {"seq": 1}


def test_events_are_isolated_per_job() -> None:
    bus = EventBus()
    sub = bus.subscribe("job1")
    bus.publish("job2", {"seq": 1})  # different job.
    with pytest.raises(queue.Empty):
        sub.get_nowait()


def test_buffer_is_bounded() -> None:
    bus = EventBus(buffer_size=3)
    for i in range(10):
        bus.publish("job1", {"seq": i})
    sub = bus.subscribe("job1")
    replayed = []
    while True:
        try:
            replayed.append(sub.get_nowait())
        except queue.Empty:
            break
    # Only the last 3 events survive the bounded buffer.
    assert replayed == [{"seq": 7}, {"seq": 8}, {"seq": 9}]


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    sub = bus.subscribe("job1")
    bus.unsubscribe("job1", sub)
    bus.publish("job1", {"seq": 1})
    with pytest.raises(queue.Empty):
        sub.get_nowait()


def test_unsubscribe_is_idempotent() -> None:
    bus = EventBus()
    sub = bus.subscribe("job1")
    bus.unsubscribe("job1", sub)
    bus.unsubscribe("job1", sub)  # no raise on second call.


def test_get_bus_returns_singleton() -> None:
    assert get_bus() is get_bus()


# ---------------------------------------------------------------------------
# SSE endpoint integration test
# ---------------------------------------------------------------------------
# The exact ordered agent events a fake pipeline publishes onto the bus.
_FAKE_EVENTS = [
    {"type": "agent", "node": "ingestion", "label": "Ingestion", "status": "running"},
    {"type": "agent", "node": "ingestion", "label": "Ingestion", "status": "done", "detail": "3 files ingested"},
    {"type": "agent", "node": "static_analysis", "label": "Static Analysis (Semgrep)", "status": "running"},
    {"type": "agent", "node": "static_analysis", "label": "Static Analysis (Semgrep)", "status": "done", "detail": "5 issues found"},
]


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def repo_dir(tmp_path) -> str:
    d = tmp_path / "repo"
    d.mkdir()
    return str(d)


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Replace graph.run_pipeline with one that streams events via the bus."""

    def _fake(repo_path: str, auto_approve: bool = True, job_id: str = None):
        bus = get_bus()
        for event in _FAKE_EVENTS:
            bus.publish(job_id, event)
        bus.publish(job_id, {"type": "status", "status": "done"})
        bus.close(job_id)
        return {"repo_path": repo_path, "final_report_json": {"quality_score": 90}}

    monkeypatch.setattr(graph, "run_pipeline", _fake)
    return _fake


def _wait_for_status(store: Store, job_id: str, target: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job and job["status"] == target:
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {target!r}")


def _parse_sse(body: str) -> list:
    """Return the parsed JSON payload of each ``data:`` frame (skipping [DONE])."""
    payloads = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        if raw == "[DONE]":
            continue
        try:
            payloads.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return payloads


def test_sse_streams_pipeline_events_in_order(store: Store, fake_pipeline, repo_dir: str) -> None:
    client = TestClient(create_app(store=store))
    job_id = client.post("/api/analyze", json={"repo_path": repo_dir}).json()["job_id"]
    # Let the background pipeline publish + close on the bus (buffered for replay).
    _wait_for_status(store, job_id, "completed")

    with client.stream("GET", f"/api/jobs/{job_id}/events") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert body.rstrip().endswith("data: [DONE]")

    payloads = _parse_sse(body)
    # The four agent events must appear in published order.
    agent_events = [p for p in payloads if p.get("type") == "agent"]
    assert agent_events == _FAKE_EVENTS
    # A terminal "done" status must be present.
    assert any(p.get("type") == "status" and p.get("status") == "done" for p in payloads)


def test_sse_unknown_job_returns_404(store: Store) -> None:
    client = TestClient(create_app(store=store))
    assert client.get("/api/jobs/nope/events").status_code == 404
