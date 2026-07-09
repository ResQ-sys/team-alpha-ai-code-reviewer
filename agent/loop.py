"""A ReAct-style agent loop that reads, searches and edits a workspace.

:func:`run_agent` drives a single-JSON-action-per-step loop over a local repo
(the *active workspace* selected in the IDE) and streams its activity to the
process-wide :class:`~observability.events.EventBus` keyed by ``run_id`` — the
same bus and SSE plumbing the review pipeline uses. Each step:

1. Ask the model (via :meth:`LLMClient.complete_schema`, which forces ollama's
   native ``format: json``) for exactly one action.
2. Publish a ``thought`` (if the model reasoned), an ``action`` (with a human
   summary), the truncated ``observation``, and — for ``edit_file`` /
   ``create_file`` — a real ``edit`` event carrying the unified diff.
3. Execute the tool, append the observation to the transcript, and repeat until
   ``finish`` or ``max_steps``.

Robustness: a malformed / actionless model reply is re-prompted once; a second
failure ends the run with an ``error`` status. Every run publishes a terminal
``status`` (``done``/``error``) and closes the bus stream.

Cancellation: :func:`request_cancel` flags a ``run_id``; the loop checks the flag
each step and, when set, finishes early with a ``done`` status. The HTTP ``stop``
endpoint calls this — ``run_agent``'s signature stays fixed by the shared
contract, so the flag lives in a module-level registry rather than a parameter.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import config
from agent import tools
from observability.events import get_bus
from observability.logging_setup import get_logger
from utils.llm_client import LLMClient

logger = get_logger("agent.loop")

# ---------------------------------------------------------------------------
# Cancellation registry (run_id -> requested). Kept module-level because the
# run_agent signature is fixed by the shared backend/frontend contract.
# ---------------------------------------------------------------------------
_CANCEL_LOCK = threading.Lock()
_CANCELLED: set[str] = set()

# How much of a tool observation to surface in the SSE stream / transcript.
_OBS_TRUNCATE = 1200

# The action verbs the model may emit.
_KNOWN_ACTIONS = {
    "list_dir",
    "read_file",
    "search",
    "edit_file",
    "create_file",
    "finish",
}

_ACTION_SCHEMA = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": sorted(_KNOWN_ACTIONS)},
        "thought": {"type": "string"},
        "path": {"type": "string"},
        "query": {"type": "string"},
        "find": {"type": "string"},
        "replace": {"type": "string"},
        "content": {"type": "string"},
        "summary": {"type": "string"},
    },
}

_SYSTEM_PROMPT = (
    "You are a coding agent working inside a single local repository. You can "
    "only see and change files in that repository. Work step by step. On EACH "
    "step reply with ONLY one JSON object describing a single action — no prose, "
    "no markdown fences.\n\n"
    "Available actions:\n"
    '  {"action":"list_dir","path":"<dir>","thought":"<why>"}\n'
    '  {"action":"read_file","path":"<file>","thought":"<why>"}\n'
    '  {"action":"search","query":"<text>","thought":"<why>"}\n'
    '  {"action":"edit_file","path":"<file>","find":"<exact snippet>","replace":"<new snippet>","thought":"<why>"}\n'
    '  {"action":"create_file","path":"<file>","content":"<full contents>","thought":"<why>"}\n'
    '  {"action":"finish","summary":"<what you did>"}\n\n'
    "Rules: paths are relative to the repository root; never use absolute paths "
    "or '..'. For edit_file, 'find' must be a snippet that occurs in the file. "
    "Inspect before you edit. When the task is complete, emit a 'finish' action "
    "with a concise prose summary of the changes you made."
)


def request_cancel(run_id: str) -> None:
    """Flag ``run_id`` for cancellation; the loop stops at its next step."""
    with _CANCEL_LOCK:
        _CANCELLED.add(run_id)


def is_cancelled(run_id: str) -> bool:
    """Return whether ``run_id`` has been flagged for cancellation."""
    with _CANCEL_LOCK:
        return run_id in _CANCELLED


def _clear_cancel(run_id: str) -> None:
    """Drop any cancellation flag for ``run_id`` (called when a run ends)."""
    with _CANCEL_LOCK:
        _CANCELLED.discard(run_id)


def _truncate(text: str, limit: int = _OBS_TRUNCATE) -> str:
    """Shorten ``text`` for the event stream / transcript."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text)} chars total)"


def _human_summary(action: str, target: str) -> str:
    """A one-line, human-readable description of an action for the UI."""
    verbs = {
        "list_dir": "Listing",
        "read_file": "Reading",
        "search": "Searching for",
        "edit_file": "Editing",
        "create_file": "Creating",
    }
    verb = verbs.get(action, action)
    if action == "search":
        return f"{verb} {target!r}"
    return f"{verb} {target}"


def run_agent(
    task: str,
    root: str,
    run_id: str,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    llm: Optional[LLMClient] = None,
) -> dict:
    """Run the agent over ``root`` for ``task``, streaming events to ``run_id``.

    Args:
        task: The natural-language instruction to carry out.
        root: Absolute path of the active workspace; every file op is confined
            to it by the tools.
        run_id: EventBus key; SSE subscribers stream this run's events.
        model: Optional model name. Honored by constructing the default
            :class:`LLMClient` with this model (ignored when ``llm`` is injected,
            e.g. by tests supplying a scripted client).
        max_steps: Hard cap on reasoning steps. Defaults to
            :data:`config.MAX_AGENT_STEPS`.
        llm: Injected LLM client (tests pass a scripted mock). Defaults to a
            real :class:`LLMClient`.

    Returns:
        ``{run_id, status, steps, files, summary}`` where ``files`` is the list
        of changed files with per-file ``added``/``removed`` counts.
    """
    bus = get_bus()
    client = llm if llm is not None else LLMClient(model=model)
    steps_cap = config.MAX_AGENT_STEPS if max_steps is None else max_steps

    # Ordered map so the final summary lists changed files deterministically.
    changed: dict[str, dict] = {}
    transcript: list[str] = [f"TASK: {task}"]
    status = "done"
    final_summary = ""
    steps_taken = 0

    bus.publish(run_id, {"type": "status", "status": "running"})

    try:
        reprompted = False
        while steps_taken < steps_cap:
            if is_cancelled(run_id):
                final_summary = "Run cancelled by user."
                bus.publish(run_id, {"type": "thought", "text": final_summary})
                break

            prompt = _build_prompt(transcript)
            parsed = client.complete_schema(_SYSTEM_PROMPT, prompt, _ACTION_SCHEMA)
            action = parsed.get("action") if isinstance(parsed, dict) else None

            if not isinstance(parsed, dict) or parsed.get("_parse_error") or action not in _KNOWN_ACTIONS:
                if reprompted:
                    status = "error"
                    final_summary = "Agent produced an unparseable action twice; stopping."
                    bus.publish(
                        run_id,
                        {"type": "status", "status": "error", "message": final_summary},
                    )
                    break
                reprompted = True
                transcript.append(
                    "OBSERVATION: Your previous reply was not a valid action JSON "
                    "object. Reply with exactly one JSON action."
                )
                continue
            reprompted = False
            steps_taken += 1

            if action == "finish":
                final_summary = (parsed.get("summary") or "").strip() or "Done."
                break

            _publish_thought(bus, run_id, parsed)
            observation, edit_event = _dispatch(root, action, parsed)

            target = _target_for(action, parsed)
            bus.publish(
                run_id,
                {
                    "type": "action",
                    "tool": action,
                    "target": target,
                    "summary": _human_summary(action, target),
                },
            )
            bus.publish(run_id, {"type": "observation", "text": _truncate(observation)})

            if edit_event is not None:
                path = edit_event["path"]
                changed[path] = {
                    "path": path,
                    "change": edit_event["change"],
                    "added": edit_event["added"],
                    "removed": edit_event["removed"],
                }
                bus.publish(run_id, edit_event)

            transcript.append(f"ACTION: {action} {target}")
            transcript.append(f"OBSERVATION: {_truncate(observation)}")
        else:
            # Loop exhausted max_steps without a finish action.
            final_summary = final_summary or (
                f"Reached the step limit ({steps_cap}) before finishing."
            )

        files = list(changed.values())
        if status != "error":
            if not final_summary:
                final_summary = "No summary produced."
            bus.publish(
                run_id,
                {"type": "summary", "text": final_summary, "files": files},
            )
            bus.publish(run_id, {"type": "status", "status": "done"})
    except Exception as exc:  # noqa: BLE001 - background boundary: never re-raise.
        logger.exception("agent run %s failed", run_id)
        status = "error"
        final_summary = f"Agent crashed: {exc}"
        bus.publish(
            run_id, {"type": "status", "status": "error", "message": final_summary}
        )
    finally:
        bus.close(run_id)
        _clear_cancel(run_id)

    return {
        "run_id": run_id,
        "status": status,
        "steps": steps_taken,
        "files": list(changed.values()),
        "summary": final_summary,
    }


def _build_prompt(transcript: list[str]) -> str:
    """Render the running transcript into the next-step user prompt."""
    body = "\n".join(transcript)
    return (
        f"{body}\n\n"
        "Given the task and the actions/observations so far, respond with the "
        "single next action as a JSON object."
    )


def _publish_thought(bus, run_id: str, parsed: dict) -> None:
    """Publish the model's short reasoning for this step, if it provided any."""
    thought = (parsed.get("thought") or parsed.get("reasoning") or "").strip()
    if thought:
        bus.publish(run_id, {"type": "thought", "text": _truncate(thought, 400)})


def _target_for(action: str, parsed: dict) -> str:
    """Extract the human 'target' (path or query) for an action."""
    if action == "search":
        return (parsed.get("query") or "").strip()
    return (parsed.get("path") or "").strip()


def _dispatch(root: str, action: str, parsed: dict) -> tuple[str, Optional[dict]]:
    """Execute ``action`` and return ``(observation_text, edit_event_or_None)``."""
    if action == "list_dir":
        return tools.list_dir(root, parsed.get("path") or "."), None
    if action == "read_file":
        return tools.read_file(root, parsed.get("path") or ""), None
    if action == "search":
        return tools.search(root, parsed.get("query") or ""), None
    if action in ("edit_file", "create_file"):
        return _dispatch_mutation(root, action, parsed)
    return f"error: unknown action {action!r}", None


def _dispatch_mutation(
    root: str, action: str, parsed: dict
) -> tuple[str, Optional[dict]]:
    """Run an ``edit_file`` / ``create_file`` tool and build its edit event."""
    path = (parsed.get("path") or "").strip()
    if action == "edit_file":
        result = tools.edit_file(
            root, path, parsed.get("find") or "", parsed.get("replace") or ""
        )
        change = "edited"
    else:
        result = tools.create_file(root, path, parsed.get("content") or "")
        change = "created"

    if not result.get("applied"):
        return f"error: {result.get('error') or 'change not applied'}", None

    observation = (
        f"{change} {path} (+{result['added']} -{result['removed']} lines)"
    )
    edit_event = {
        "type": "edit",
        "path": path,
        "change": change,
        "diff": result["diff"],
        "added": result["added"],
        "removed": result["removed"],
    }
    return observation, edit_event
