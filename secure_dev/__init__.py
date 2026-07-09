"""Secure autonomous fix-application subsystem.

This package turns verified/approved review findings into real changes on
disk in a safe, reversible way:

- ``git_ops``  : thin, timeout-guarded wrappers around git plus in-memory
                 snapshot/restore for rollback.
- ``patch``    : diff generation and indentation-tolerant code replacement.
- ``fix_applier`` (owned elsewhere) : orchestrates applying a batch of fixes.
- ``repair_loop`` (owned elsewhere) : re-scans and self-heals a fix.

Only ``git_ops`` and ``patch`` are provided by this module's owner.
"""

from __future__ import annotations

__all__ = ["git_ops", "patch"]
