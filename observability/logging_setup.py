"""Structured logging setup for the AI code-review pipeline.

Provides a single idempotent entrypoint (:func:`configure_logging`) that wires
up the root logger once, a :func:`get_logger` accessor, and a :class:`timed`
context manager for measuring and logging block durations.

Uses ``rich`` for colorized console output when available, and falls back to a
plain structured ``logging.Formatter`` otherwise. Nothing here raises on
import, and configuration is safe to call multiple times.

Public contract:
    configure_logging(level: str = "INFO") -> None
    get_logger(name: str) -> logging.Logger
    class timed(contextlib.AbstractContextManager)
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Optional

try:  # Optional pretty console handler.
    from rich.logging import RichHandler

    _HAS_RICH = True
except Exception:  # pragma: no cover - rich is an optional dependency.
    RichHandler = None  # type: ignore[assignment]
    _HAS_RICH = False

__all__ = ["configure_logging", "get_logger", "timed"]

# Marks handlers this module installs so re-configuration is idempotent and
# never clobbers handlers owned by other libraries.
_OWNED_HANDLER_FLAG = "_ai_review_owned"

_PLAIN_FORMAT = "%(asctime)s %(levelname)-8s %(name)s :: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_config_lock = threading.Lock()
_configured = False


def _resolve_level(level: str) -> int:
    """Translate a level name (or number-as-string) into a logging int."""
    if isinstance(level, int):
        return level
    name = (level or "INFO").strip().upper()
    resolved = logging.getLevelName(name)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _build_handler() -> logging.Handler:
    """Create the console handler, preferring rich when it is installed."""
    if _HAS_RICH:
        handler: logging.Handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
            markup=False,
        )
        # Rich renders time/level itself; keep the format lean.
        handler.setFormatter(logging.Formatter("%(name)s :: %(message)s"))
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(fmt=_PLAIN_FORMAT, datefmt=_DATE_FORMAT)
        )
    setattr(handler, _OWNED_HANDLER_FLAG, True)
    return handler


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once; idempotent and thread-safe.

    Repeated calls only update the level, never stacking handlers. Handlers
    installed by other code are left untouched.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``) or int.
    """
    global _configured
    resolved = _resolve_level(level)
    with _config_lock:
        root = logging.getLogger()
        root.setLevel(resolved)

        already_owned = any(
            getattr(h, _OWNED_HANDLER_FLAG, False) for h in root.handlers
        )
        if not already_owned:
            handler = _build_handler()
            handler.setLevel(resolved)
            root.addHandler(handler)
        else:
            for handler in root.handlers:
                if getattr(handler, _OWNED_HANDLER_FLAG, False):
                    handler.setLevel(resolved)

        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring logging is configured first.

    Args:
        name: Logger name, conventionally the module ``__name__``.

    Returns:
        A ``logging.Logger`` ready for use.
    """
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


class timed(contextlib.AbstractContextManager):
    """Context manager that logs the wall-clock duration of a block.

    Example:
        >>> with timed("semgrep scan"):
        ...     run_scan()

    On exit it emits ``"<label> took <n> ms"`` at INFO, or at ERROR with the
    exception type appended when the block raised. The exception is never
    suppressed.
    """

    def __init__(self, label: str, logger: Optional[logging.Logger] = None) -> None:
        """Create a timer.

        Args:
            label: Human-readable name of the timed block.
            logger: Logger to emit to; defaults to this module's logger.
        """
        self.label = label
        self.logger = logger or get_logger("observability.timed")
        self._start: Optional[float] = None
        self.elapsed_ms: Optional[float] = None

    def __enter__(self) -> "timed":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        start = self._start if self._start is not None else time.perf_counter()
        self.elapsed_ms = (time.perf_counter() - start) * 1000.0
        if exc_type is None:
            self.logger.info("%s took %.1f ms", self.label, self.elapsed_ms)
        else:
            self.logger.error(
                "%s failed after %.1f ms (%s)",
                self.label,
                self.elapsed_ms,
                exc_type.__name__,
            )
        # Never suppress exceptions.
        return False
