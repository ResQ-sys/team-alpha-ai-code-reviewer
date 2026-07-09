"""Tests for the FastAPI surface in :mod:`server.api`.

All tests use ``fastapi.testclient.TestClient`` with an in-memory
:class:`persistence.store.Store`. ``graph.run_pipeline`` and
``secure_dev.fix_applier.apply_fixes`` are stubbed/monkeypatched — the real
pipeline, LLM, filesystem, and network are never touched.
"""

from __future__ import annotations

import sys
import time
import types
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

import graph
from persistence.store import Store
from server.api import create_app

# A minimal, fast report payload mimicking the pipeline's final_report_json.
_STUB_REPORT = {
    "repo_path": "/fake/repo",
    "quality_score": 88,
    "suggested_fixes": [
        {
            "file": "app.py",
            "line": 10,
            "severity": "ERROR",
            "rule_id": "python.lang.security.audit.dangerous-eval",
            "explanation": "Avoid eval on untrusted input.",
            "original_code": "eval(x)",
            "fixed_code": "ast.literal_eval(x)",
            "verified": True,
            "verification_notes": "rule no longer fires",
        }
    ],
}


@pytest.fixture()
def store() -> Store:
    """In-memory store, closed after the test."""
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def stub_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Replace graph.run_pipeline with a fast, deterministic stub."""

    def _fast(repo_path: str, auto_approve: bool = True) -> dict:
        return {"repo_path": repo_path, "final_report_json": dict(_STUB_REPORT)}

    monkeypatch.setattr(graph, "run_pipeline", _fast)
    return _fast


@pytest.fixture()
def repo_dir(tmp_path) -> str:
    """A real, existing directory usable as a valid ``repo_path``."""
    d = tmp_path / "repo"
    d.mkdir()
    return str(d)


@pytest.fixture()
def client(store: Store, stub_pipeline) -> TestClient:
    """TestClient wired to the in-memory store and stubbed pipeline."""
    app = create_app(store=store)
    return TestClient(app)


def _wait_for_status(store: Store, job_id: str, target: str, timeout: float = 5.0) -> dict:
    """Poll the store until ``job_id`` reaches ``target`` (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job and job["status"] == target:
            return job
        time.sleep(0.02)
    raise AssertionError(
        f"job {job_id} never reached {target!r}; last={store.get_job(job_id)}"
    )


# -- health ---------------------------------------------------------------
def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- analyze -> job lifecycle ---------------------------------------------
def test_analyze_returns_job_id(client: TestClient, repo_dir: str) -> None:
    resp = client.post("/api/analyze", json={"repo_path": repo_dir})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    assert isinstance(job_id, str) and len(job_id) == 36


def test_analyze_rejects_blank_repo_path(client: TestClient) -> None:
    resp = client.post("/api/analyze", json={"repo_path": ""})
    assert resp.status_code == 422


def test_analyze_rejects_nonexistent_repo_path(client: TestClient) -> None:
    resp = client.post("/api/analyze", json={"repo_path": "/no/such/dir/xyz"})
    assert resp.status_code == 400


def test_analyze_rejects_path_outside_allowed_root(store: Store, stub_pipeline, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    client = TestClient(create_app(store=store, allowed_root=str(root)))
    resp = client.post("/api/analyze", json={"repo_path": str(outside)})
    assert resp.status_code == 403


def test_analyze_accepts_path_inside_allowed_root(store: Store, stub_pipeline, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    inside = root / "proj"
    inside.mkdir()
    client = TestClient(create_app(store=store, allowed_root=str(root)))
    resp = client.post("/api/analyze", json={"repo_path": str(inside)})
    assert resp.status_code == 200


def test_api_key_enforced_when_configured(store: Store, stub_pipeline, repo_dir: str) -> None:
    client = TestClient(create_app(store=store, api_key="s3cret"))
    # Missing key -> 401.
    assert client.post("/api/analyze", json={"repo_path": repo_dir}).status_code == 401
    # Correct key -> allowed.
    ok = client.post(
        "/api/analyze", json={"repo_path": repo_dir}, headers={"X-API-Key": "s3cret"}
    )
    assert ok.status_code == 200


def test_job_runs_to_completion_and_saves_report(client: TestClient, store: Store, repo_dir: str) -> None:
    job_id = client.post("/api/analyze", json={"repo_path": repo_dir}).json()["job_id"]
    job = _wait_for_status(store, job_id, "completed")
    assert job["report"]["quality_score"] == 88
    assert job["report"]["suggested_fixes"][0]["rule_id"].startswith("python")


def test_get_job_via_api_after_completion(client: TestClient, store: Store, repo_dir: str) -> None:
    job_id = client.post("/api/analyze", json={"repo_path": repo_dir}).json()["job_id"]
    _wait_for_status(store, job_id, "completed")
    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_get_unknown_job_404(client: TestClient) -> None:
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs(client: TestClient, store: Store, tmp_path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    client.post("/api/analyze", json={"repo_path": str(a)})
    client.post("/api/analyze", json={"repo_path": str(b)})
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 2


# -- error propagation ----------------------------------------------------
def test_pipeline_error_recorded(store: Store, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def _boom(repo_path: str, auto_approve: bool = True):
        raise RuntimeError("semgrep exploded")

    monkeypatch.setattr(graph, "run_pipeline", _boom)
    client = TestClient(create_app(store=store))
    job_id = client.post("/api/analyze", json={"repo_path": str(tmp_path)}).json()["job_id"]
    job = _wait_for_status(store, job_id, "error")
    assert "semgrep exploded" in job["error"]


# -- SSE events -----------------------------------------------------------
def test_events_stream_reaches_terminal(client: TestClient, store: Store, repo_dir: str) -> None:
    # The default stub_pipeline never touches the bus; the SSE endpoint must
    # still synthesise a terminal "done" status from the store and end cleanly.
    job_id = client.post("/api/analyze", json={"repo_path": repo_dir}).json()["job_id"]
    _wait_for_status(store, job_id, "completed")
    with client.stream("GET", f"/api/jobs/{job_id}/events") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "data:" in body
    assert '"status": "done"' in body
    assert body.rstrip().endswith("data: [DONE]")


def test_events_unknown_job_404(client: TestClient) -> None:
    resp = client.get("/api/jobs/nope/events")
    assert resp.status_code == 404


# -- approvals ------------------------------------------------------------
def test_approvals_persist(client: TestClient, store: Store) -> None:
    job_id = store.create_job("/fake/repo")
    decisions = {"app.py:10": "approve"}
    resp = client.post("/api/approvals", json={"job_id": job_id, "decisions": decisions})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    job = store.get_job(job_id)
    assert job["decisions"] == decisions
    assert job["status"] == "approved"


def test_approvals_unknown_job_404(client: TestClient) -> None:
    resp = client.post("/api/approvals", json={"job_id": "nope", "decisions": {}})
    assert resp.status_code == 404


# -- apply ----------------------------------------------------------------
@dataclass
class _FakeApplyResult:
    applied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    combined_diff: str = ""
    commit: str | None = None
    dry_run: bool = True


@pytest.fixture()
def fake_fix_applier(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake ``secure_dev.fix_applier`` module into ``sys.modules``."""
    calls = {}

    def _apply_fixes(repo_path, suggested_fixes, *, dry_run=True, **kwargs):
        calls["repo_path"] = repo_path
        calls["fixes"] = suggested_fixes
        calls["dry_run"] = dry_run
        return _FakeApplyResult(
            applied=[{"file": f["file"], "line": f["line"]} for f in suggested_fixes],
            combined_diff="--- a\n+++ b\n",
            dry_run=dry_run,
        )

    module = types.ModuleType("secure_dev.fix_applier")
    module.apply_fixes = _apply_fixes
    monkeypatch.setitem(sys.modules, "secure_dev.fix_applier", module)
    return calls


def test_apply_dry_run(client: TestClient, store: Store, fake_fix_applier) -> None:
    job_id = store.create_job("/fake/repo")
    store.save_report(job_id, dict(_STUB_REPORT))
    resp = client.post("/api/apply", json={"job_id": job_id, "dry_run": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["applied"] == [{"file": "app.py", "line": 10}]
    # dry-run must not flip status to "applied".
    assert store.get_job(job_id)["status"] == "pending"
    # applied records are persisted regardless.
    assert store.get_job(job_id)["applied"] == [{"file": "app.py", "line": 10}]
    assert fake_fix_applier["dry_run"] is True


def test_apply_real_sets_applied_status(client: TestClient, store: Store, fake_fix_applier) -> None:
    job_id = store.create_job("/fake/repo")
    store.save_report(job_id, dict(_STUB_REPORT))
    resp = client.post("/api/apply", json={"job_id": job_id, "dry_run": False})
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is False
    assert store.get_job(job_id)["status"] == "applied"
    assert fake_fix_applier["dry_run"] is False


def test_apply_unknown_job_404(client: TestClient, fake_fix_applier) -> None:
    resp = client.post("/api/apply", json={"job_id": "nope", "dry_run": True})
    assert resp.status_code == 404
