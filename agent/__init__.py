"""Agent-mode package: root-scoped tools + a ReAct loop over a live workspace.

The agent operates on the *active workspace* selected via ``/api/workspace`` and
streams its reasoning/actions/edits over the shared
:class:`~observability.events.EventBus` (keyed by ``run_id``) — the same SSE
plumbing the review pipeline uses. Every filesystem access is confined to the
active root by :func:`secure_dev.safe_path.resolve_within`.
"""

from __future__ import annotations

from agent import tools  # noqa: F401 - re-export for convenience.
from agent.loop import is_cancelled, request_cancel, run_agent

__all__ = ["run_agent", "request_cancel", "is_cancelled", "tools"]
