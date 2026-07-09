"""Observability subsystem: structured logging and timing helpers.

Public surface (see ``logging_setup``):
    configure_logging(level="INFO") -> None
    get_logger(name) -> logging.Logger
    timed(label, logger=None)  # context manager, logs duration on exit
"""

from observability.logging_setup import configure_logging, get_logger, timed

__all__ = ["configure_logging", "get_logger", "timed"]
