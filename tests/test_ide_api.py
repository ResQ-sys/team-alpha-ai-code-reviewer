"""Tests for the IDE-companion surface in :mod:`server.ide_api`.

The IDE routes are registered onto the existing review app, so these tests also
implicitly confirm the two surfaces coexist on one FastAPI instance.

Ollama HTTP calls (``requests.get`` / ``requests.post``) are monkeypatched with
fakes — no live Ollama, network, or model download ever runs. Every filesystem
test operates inside a per-test ``tmp_path`` workspace so nothing outside the
sandbox is read or written.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import graph
import server.ide_api as ide_api
from persistence.store import Store
from server.ide_api import create_ide_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def store() -> Store:
    """In-memory store, closed after the test."""
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Keep the review pipeline inert so app construction never touches an LLM."""

    def _fast(repo_path: str, auto_approve: bool = True) -> dict:
        return {"repo_path": repo_path, "final_report_json": {}}

    monkeypatch.setattr(graph, "run_pipeline", _fast)


@pytest.fixture()
def workspace(tmp_path):
    """A populated tmp workspace: junk dirs to skip + a real nested file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    # Junk that must be excluded from the tree.
    for junk in (".git", "node_modules", "__pycache__", ".venv", "dist"):
        d = tmp_path / junk
        d.mkdir()
        (d / "noise.txt").write_text("x", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(store: Store, workspace) -> TestClient:
    """TestClient wired to the in-memory store and the tmp workspace root."""
    app = create_ide_app(store=store, workspace_root=str(workspace))
    return TestClient(app)


class _FakeResp:
    """Minimal stand-in for a ``requests`` Response."""

    def __init__(self, *, json_data=None, lines=None, raise_exc=None):
        self._json = json_data
        self._lines = lines or []
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._json

    def iter_lines(self):
        for line in self._lines:
            yield line


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------
def test_models_lists_ollama_tags(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def _fake_get(url, timeout=None, **kw):
        assert url.endswith("/api/tags")
        return _FakeResp(json_data={"models": [{"name": "llama3.2:1b"}, {"name": "qwen2.5-coder"}]})

    monkeypatch.setattr(ide_api.requests, "get", _fake_get)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json()["models"] == ["llama3.2:1b", "qwen2.5-coder"]


def test_models_empty_on_ollama_down(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def _boom(url, timeout=None, **kw):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(ide_api.requests, "get", _boom)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": []}


# ---------------------------------------------------------------------------
# GET /api/fs/tree
# ---------------------------------------------------------------------------
def test_tree_excludes_junk_and_uses_relative_paths(client: TestClient):
    resp = client.get("/api/fs/tree")
    assert resp.status_code == 200
    tree = resp.json()
    assert tree["type"] == "directory"
    assert tree["path"] == ""  # root is relative-empty

    names = {child["name"] for child in tree["children"]}
    assert "src" in names
    assert "README.md" in names
    # Junk directories are pruned.
    assert names.isdisjoint({".git", "node_modules", "__pycache__", ".venv", "dist"})

    src = next(c for c in tree["children"] if c["name"] == "src")
    assert src["type"] == "directory"
    main = src["children"][0]
    assert main["name"] == "main.py"
    assert main["type"] == "file"
    # Paths are relative to the workspace root (no leading slash, no tmp prefix).
    assert main["path"] == "src/main.py"


def test_tree_rejects_traversal(client: TestClient):
    resp = client.get("/api/fs/tree", params={"root": "../../etc"})
    assert resp.status_code == 400


def test_tree_subdir_root(client: TestClient):
    resp = client.get("/api/fs/tree", params={"root": "src"})
    assert resp.status_code == 200
    tree = resp.json()
    assert tree["name"] == "src"
    assert tree["path"] == "src"
    assert tree["children"][0]["path"] == "src/main.py"


# ---------------------------------------------------------------------------
# GET / PUT /api/fs/file
# ---------------------------------------------------------------------------
def test_file_read(client: TestClient):
    resp = client.get("/api/fs/file", params={"path": "src/main.py"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "src/main.py"
    assert body["content"] == "print('hi')\n"
    assert body["language"] == "python"


def test_file_read_missing_404(client: TestClient):
    resp = client.get("/api/fs/file", params={"path": "src/nope.py"})
    assert resp.status_code == 404


def test_file_read_rejects_traversal(client: TestClient):
    resp = client.get("/api/fs/file", params={"path": "../../etc/passwd"})
    assert resp.status_code == 400


def test_file_write_read_round_trip(client: TestClient, workspace):
    new_rel = "src/new/module.py"
    payload = {"path": new_rel, "content": "x = 42\n"}
    put = client.put("/api/fs/file", json=payload)
    assert put.status_code == 200
    assert put.json() == {"ok": True}
    # It really landed on disk inside the workspace.
    assert (workspace / "src" / "new" / "module.py").read_text(encoding="utf-8") == "x = 42\n"
    # And round-trips through the read endpoint.
    got = client.get("/api/fs/file", params={"path": new_rel})
    assert got.status_code == 200
    assert got.json()["content"] == "x = 42\n"


def test_file_write_rejects_traversal(client: TestClient, workspace):
    resp = client.put("/api/fs/file", json={"path": "../evil.py", "content": "pwned"})
    assert resp.status_code == 400
    assert not (workspace.parent / "evil.py").exists()


def test_file_write_rejects_absolute(client: TestClient):
    resp = client.put("/api/fs/file", json={"path": "/etc/evil", "content": "pwned"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/chat  (SSE)
# ---------------------------------------------------------------------------
def _chat_lines(tokens):
    """Build Ollama-style NDJSON chat lines ending with a done sentinel."""
    lines = [json.dumps({"message": {"role": "assistant", "content": t}, "done": False}) for t in tokens]
    lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
    return lines


def test_chat_streams_sse_tokens(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def _fake_post(url, json=None, stream=None, timeout=None, **kw):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResp(lines=_chat_lines(["Hello", ", ", "world"]))

    monkeypatch.setattr(ide_api.requests, "post", _fake_post)
    resp = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "model": "m", "file_context": "print(1)"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "data: Hello\n\n" in body
    assert "data: world\n\n" in body
    assert body.rstrip().endswith("data: [DONE]")

    assert captured["url"].endswith("/api/chat")
    # A system message is prepended, and the file_context is threaded in.
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert "print(1)" in captured["payload"]["messages"][0]["content"]
    assert captured["payload"]["stream"] is True


def test_chat_error_event_when_ollama_down(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def _boom(url, **kw):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(ide_api.requests, "post", _boom)
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    assert "data: [DONE]" in body


# ---------------------------------------------------------------------------
# POST /api/models/pull  (SSE)
# ---------------------------------------------------------------------------
def _pull_lines():
    """Ollama-style NDJSON pull progress ending in success."""
    return [
        json.dumps({"status": "pulling manifest"}),
        json.dumps({"status": "downloading", "completed": 50, "total": 100}),
        json.dumps({"status": "success"}),
    ]


def test_pull_model_streams_progress(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def _fake_post(url, json=None, stream=None, timeout=None, **kw):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResp(lines=_pull_lines())

    monkeypatch.setattr(ide_api.requests, "post", _fake_post)
    resp = client.post("/api/models/pull", json={"name": "qwen2.5-coder:7b"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert '"status": "pulling manifest"' in body
    assert '"percent": 50.0' in body
    assert '"status": "success"' in body
    assert body.rstrip().endswith("data: [DONE]")

    assert captured["url"].endswith("/api/pull")
    assert captured["payload"]["name"] == "qwen2.5-coder:7b"
    assert captured["payload"]["stream"] is True


def test_pull_model_error_frame_when_ollama_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def _boom(url, **kw):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(ide_api.requests, "post", _boom)
    resp = client.post("/api/models/pull", json={"name": "whatever"})
    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    assert "data: [DONE]" in body


# ---------------------------------------------------------------------------
# POST /api/edit  (SSE)
# ---------------------------------------------------------------------------
def test_edit_streams_only_code(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def _fake_post(url, json=None, stream=None, timeout=None, **kw):
        captured["payload"] = json
        return _FakeResp(lines=_chat_lines(["def f():\n", "    return 1\n"]))

    monkeypatch.setattr(ide_api.requests, "post", _fake_post)
    resp = client.post(
        "/api/edit",
        json={"code": "def f(): pass", "instruction": "make it return 1", "language": "python", "model": "m"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "data: def f():\n" in body
    assert "return 1" in body
    assert body.rstrip().endswith("data: [DONE]")

    system = captured["payload"]["messages"][0]
    assert system["role"] == "system"
    assert system["content"] == "Return only the edited code, no fences, no prose."


# ---------------------------------------------------------------------------
# coexistence: review routes still present on the same app
# ---------------------------------------------------------------------------
def test_review_routes_coexist(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
