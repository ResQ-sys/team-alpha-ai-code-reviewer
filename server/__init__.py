"""HTTP surface for the AI code-review pipeline.

Exposes a FastAPI application (:data:`server.api.app`) plus a
:func:`server.api.create_app` factory for tests and embedding.
"""

from __future__ import annotations

from server.api import app, create_app

__all__ = ["app", "create_app"]
