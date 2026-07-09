"""Path-containment guard shared by the fix-application layer.

The ``file`` field of a suggested fix originates from LLM output derived from an
*untrusted* repository under review. A crafted repo can induce a fix whose
``file`` is absolute (``/etc/passwd``), contains traversal (``../../x``), or
points at an in-repo symlink that resolves outside the tree. Left unchecked,
applying such a fix reads/overwrites arbitrary files on the host.

Every read/write in :mod:`secure_dev.patch`, :mod:`secure_dev.fix_applier`, and
:mod:`secure_dev.git_ops` funnels the (repo, rel_file) pair through
:func:`resolve_within` first, which canonicalizes both sides and refuses any
target that escapes the repository — including via symlink.

Public contract (consumed by patch / fix_applier / git_ops)::

    class PathEscapeError(ValueError)
    resolve_within(base, rel) -> str   # canonical absolute path, guaranteed inside base
    is_within(base, rel) -> bool       # non-raising predicate wrapper
"""

from __future__ import annotations

import os


class PathEscapeError(ValueError):
    """Raised when a relative path would resolve outside its base repo."""


def resolve_within(base: str, rel: str) -> str:
    """Resolve *rel* against *base* and guarantee the result stays inside *base*.

    Canonicalizes *base* and the joined target with :func:`os.path.realpath`
    (which follows symlinks and collapses ``..``), then verifies the target is
    *base* itself or a descendant of it.

    Args:
        base: Repository root. Must be a non-empty path.
        rel: Repo-relative file path taken from untrusted fix output.

    Returns:
        The canonical absolute path of the target, guaranteed within *base*.

    Raises:
        PathEscapeError: if *rel* is empty, absolute, traverses above *base*,
            or (via a symlink) resolves outside *base*.
    """
    if not base:
        raise PathEscapeError("base repository path is empty")
    if not rel:
        raise PathEscapeError("empty relative path")
    if os.path.isabs(rel):
        raise PathEscapeError(f"absolute path not allowed: {rel!r}")

    # Reject explicit upward traversal before touching the filesystem so a
    # missing intermediate directory cannot mask an escape attempt.
    normalized = os.path.normpath(rel)
    if normalized == os.pardir or normalized.startswith(os.pardir + os.sep):
        raise PathEscapeError(f"path traversal not allowed: {rel!r}")

    base_real = os.path.realpath(base)
    target_real = os.path.realpath(os.path.join(base_real, rel))

    if target_real == base_real:
        return target_real
    try:
        if os.path.commonpath([base_real, target_real]) != base_real:
            raise PathEscapeError(f"path escapes repository: {rel!r}")
    except ValueError as exc:  # different drive/root -> not containable.
        raise PathEscapeError(f"path escapes repository: {rel!r}") from exc

    return target_real


def is_within(base: str, rel: str) -> bool:
    """Non-raising predicate: True iff :func:`resolve_within` would succeed."""
    try:
        resolve_within(base, rel)
        return True
    except PathEscapeError:
        return False
