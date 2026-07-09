"""Tests for agent-mode: root-scoped tools, the ReAct loop, and workspace routes.

No live LLM, Ollama, or network is touched: the loop is driven by a scripted
mock :class:`ScriptedLLM`, and the review pipeline is stubbed inert during app
construction. Every filesystem test runs inside a per-test ``tmp_path``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import graph
from agent import loop as agent_loop
from agent import tools
from agent.loop import run_agent
from observability.events import CLOSE, get_bus
from persistence.store import Store
from server.ide_api import create_ide_app


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Keep the review pipeline inert so app construction never touches an LLM."""

    def _fast(repo_path: str, auto_approve: bool = True) -> dict:
        return {"repo_path": repo_path, "final_report_json": {}}

    monkeypatch.setattr(graph, "run_pipeline", _fast)


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


class ScriptedLLM:
    """A mock LLM that returns a pre-scripted action for each loop step."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def complete_schema(self, system: str, prompt: str, schema: dict, **_kw) -> dict:
        self.prompts.append(prompt)
        if not self._responses:
            return {"action": "finish", "summary": "out of script"}
        return self._responses.pop(0)


def _drain_events(run_id: str) -> list[dict]:
    """Subscribe and collect every event published for ``run_id`` up to CLOSE.

    Must be called *after* the (synchronous) run so the closed job replays its
    full buffered history and the CLOSE sentinel terminates the drain.
    """
    subscriber = get_bus().subscribe(run_id)
    events: list[dict] = []
    while True:
        item = subscriber.get(timeout=2.0)
        if item is CLOSE:
            break
        events.append(item)
    return events


# ---------------------------------------------------------------------------
# Tools — read-only inspection
# ---------------------------------------------------------------------------
def test_list_dir_lists_entries_and_marks_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()

    out = tools.list_dir(str(tmp_path))

    assert "src/" in out
    assert "a.py" in out
    assert "__pycache__" not in out  # junk dir skipped


def test_read_file_returns_contents(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")

    assert tools.read_file(str(tmp_path), "hello.py") == "print('hi')\n"


def test_read_file_truncates_over_cap(tmp_path):
    (tmp_path / "big.txt").write_text("y" * 500, encoding="utf-8")

    out = tools.read_file(str(tmp_path), "big.txt", max_bytes=100)

    assert out.startswith("y" * 100)
    assert "truncated" in out


def test_search_finds_matches(tmp_path):
    (tmp_path / "a.py").write_text("alpha\nNEEDLE here\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("nothing\n", encoding="utf-8")

    out = tools.search(str(tmp_path), "NEEDLE")

    assert "NEEDLE" in out
    assert "a.py" in out


def test_search_no_matches(tmp_path):
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")

    assert "no matches" in tools.search(str(tmp_path), "zzz-not-present")


# ---------------------------------------------------------------------------
# Tools — mutation
# ---------------------------------------------------------------------------
def test_edit_file_applies_and_reports_diff(tmp_path):
    target = tmp_path / "t.py"
    target.write_text("value = old\n", encoding="utf-8")

    result = tools.edit_file(str(tmp_path), "t.py", "old", "new")

    assert result["applied"] is True
    assert target.read_text(encoding="utf-8") == "value = new\n"
    assert result["added"] == 1
    assert result["removed"] == 1
    assert "new" in result["diff"]


def test_edit_file_missing_snippet_not_applied(tmp_path):
    (tmp_path / "t.py").write_text("value = old\n", encoding="utf-8")

    result = tools.edit_file(str(tmp_path), "t.py", "absent", "x")

    assert result["applied"] is False
    assert result["error"]


def test_create_file_writes_and_diffs_whole_file(tmp_path):
    result = tools.create_file(str(tmp_path), "pkg/new.py", "print('made')\n")

    assert result["applied"] is True
    assert (tmp_path / "pkg" / "new.py").read_text(encoding="utf-8") == "print('made')\n"
    assert result["added"] == 1
    assert result["removed"] == 0


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/passwd", "../../x"])
def test_tools_reject_traversal(tmp_path, bad):
    assert tools.read_file(str(tmp_path), bad).startswith("error:")
    assert tools.list_dir(str(tmp_path), bad).startswith("error:")
    assert tools.edit_file(str(tmp_path), bad, "a", "b")["applied"] is False
    assert tools.create_file(str(tmp_path), bad, "x")["applied"] is False


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
def test_run_agent_reads_edits_then_finishes(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("greeting = old\n", encoding="utf-8")

    llm = ScriptedLLM(
        [
            {"action": "read_file", "path": "target.py", "thought": "inspect it"},
            {
                "action": "edit_file",
                "path": "target.py",
                "find": "old",
                "replace": "new",
                "thought": "apply the fix",
            },
            {"action": "finish", "summary": "Replaced old with new."},
        ]
    )

    result = run_agent("Rename old to new", str(tmp_path), "run-loop-1", llm=llm)

    # File actually changed on disk.
    assert target.read_text(encoding="utf-8") == "greeting = new\n"

    # Return value reports the change accurately.
    assert result["status"] == "done"
    assert result["summary"] == "Replaced old with new."
    assert result["files"] == [
        {"path": "target.py", "change": "edited", "added": 1, "removed": 1}
    ]

    # Events include action + edit + summary, in order.
    events = _drain_events("run-loop-1")
    types = [e["type"] for e in events]
    assert types[0] == "status" and events[0]["status"] == "running"
    assert "action" in types and "edit" in types and "summary" in types
    assert types.index("action") < types.index("edit") < types.index("summary")
    assert types[-1] == "status" and events[-1]["status"] == "done"

    # The edit event carries a real diff + counts.
    edit = next(e for e in events if e["type"] == "edit")
    assert edit["path"] == "target.py"
    assert edit["change"] == "edited"
    assert edit["added"] == 1 and edit["removed"] == 1
    assert "new" in edit["diff"]

    # The summary event lists the changed file.
    summary = next(e for e in events if e["type"] == "summary")
    assert summary["files"] == result["files"]


def test_run_agent_reprompts_then_errors_on_malformed(tmp_path):
    llm = ScriptedLLM(
        [
            {"_parse_error": True, "_raw": "junk"},
            {"nonsense": "still bad"},
        ]
    )

    result = run_agent("do it", str(tmp_path), "run-bad-1", llm=llm)

    assert result["status"] == "error"
    events = _drain_events("run-bad-1")
    assert events[-1]["type"] == "status" and events[-1]["status"] == "error"


def test_run_agent_stops_when_cancelled(tmp_path):
    agent_loop.request_cancel("run-cancel-1")
    llm = ScriptedLLM([{"action": "list_dir", "path": "."}])

    result = run_agent("browse", str(tmp_path), "run-cancel-1", llm=llm)

    # Cancelled before executing any step; ends cleanly as done.
    assert result["status"] == "done"
    assert result["steps"] == 0


# ---------------------------------------------------------------------------
# Workspace routes
# ---------------------------------------------------------------------------
def test_get_workspace_returns_active_root(store, tmp_path):
    app = create_ide_app(store, workspace_root=str(tmp_path), api_key="")
    client = TestClient(app)

    resp = client.get("/api/workspace")

    assert resp.status_code == 200
    body = resp.json()
    assert body["root"].endswith(tmp_path.name)
    assert body["name"] == tmp_path.name


def test_post_workspace_opens_directory(store, tmp_path):
    other = tmp_path / "project-b"
    other.mkdir()
    (other / "main.py").write_text("print(1)\n", encoding="utf-8")

    app = create_ide_app(store, workspace_root=str(tmp_path), api_key="")
    client = TestClient(app)

    resp = client.post("/api/workspace", json={"path": str(other)})

    assert resp.status_code == 200
    assert resp.json()["name"] == "project-b"

    # The active root actually moved: the file tree now reflects project-b.
    tree = client.get("/api/fs/tree").json()
    names = {child["name"] for child in tree["children"]}
    assert "main.py" in names


def test_post_workspace_rejects_non_directory(store, tmp_path):
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x", encoding="utf-8")

    app = create_ide_app(store, workspace_root=str(tmp_path), api_key="")
    client = TestClient(app)

    resp = client.post("/api/workspace", json={"path": str(a_file)})

    assert resp.status_code == 400


def test_post_workspace_requires_api_key_when_configured(store, tmp_path):
    app = create_ide_app(store, workspace_root=str(tmp_path), api_key="secret")
    client = TestClient(app)

    resp = client.post("/api/workspace", json={"path": str(tmp_path)})

    assert resp.status_code == 401
