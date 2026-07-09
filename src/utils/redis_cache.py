"""
Redis caching layer.

Primary use: cache Code-LLM completions so repeat pipeline runs (same file +
same prompt + same model) are instant and free instead of re-querying the LLM.

Connection strategy (graceful degradation, same spirit as the RAG FAISS→TF-IDF
fallback):
    1. Try a standalone Redis server at REDIS_URL.
    2. If unreachable, spin up an embedded `redislite` server (no system daemon
       needed — great for a hackathon / judging box).
    3. If neither is available, become a transparent no-op so the pipeline
       still runs (just without caching).

The cache can be toggled at runtime (e.g. from the Streamlit sidebar) via
`set_enabled(bool)` without touching any agent code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from config import (
    REDIS_ENABLED,
    REDIS_KEY_PREFIX,
    REDIS_LITE_PATH,
    REDIS_TTL,
    REDIS_URL,
)


class RedisCache:
    def __init__(self):
        self.client = None
        self.backend = "disabled"      # disabled | redis-server | redislite | unavailable
        self.hits = 0
        self.misses = 0
        self._connected = False

    # ------------------------------------------------------------------
    def _ensure_connection(self):
        """Lazily connect on first use so importing this module is cheap and
        side-effect free (important for the mocked test suite)."""
        if self._connected:
            return
        self._connected = True

        # 1) Standalone Redis
        try:
            import redis

            client = redis.Redis.from_url(
                REDIS_URL, socket_connect_timeout=0.5, decode_responses=True
            )
            client.ping()
            self.client = client
            self.backend = "redis-server"
            return
        except Exception:
            pass

        # 2) Embedded redislite
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from redislite import Redis

            client = Redis(REDIS_LITE_PATH, decode_responses=True)
            client.ping()
            self.client = client
            self.backend = "redislite"
            return
        except Exception:
            pass

        # 3) No-op
        self.client = None
        self.backend = "unavailable"

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        self._ensure_connection()
        return self.client is not None

    @staticmethod
    def _key(namespace: str, payload: Any) -> str:
        blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()
        return f"{REDIS_KEY_PREFIX}:{namespace}:{digest}"

    # ------------------------------------------------------------------
    def get(self, namespace: str, payload: Any) -> Optional[str]:
        if not self.available:
            return None
        try:
            val = self.client.get(self._key(namespace, payload))
        except Exception:
            return None
        if val is None:
            self.misses += 1
            return None
        self.hits += 1
        return val

    def set(self, namespace: str, payload: Any, value: str, ttl: int = REDIS_TTL):
        if not self.available:
            return
        try:
            self.client.set(self._key(namespace, payload), value, ex=ttl)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def stats(self) -> dict:
        self._ensure_connection()
        n_keys = None
        if self.client is not None:
            try:
                n_keys = self.client.dbsize()
            except Exception:
                n_keys = None
        return {
            "backend": self.backend,
            "available": self.client is not None,
            "hits": self.hits,
            "misses": self.misses,
            "keys": n_keys,
        }

    def clear(self) -> int:
        """Delete all keys under our prefix. Returns number removed."""
        if not self.available:
            return 0
        try:
            keys = list(self.client.scan_iter(match=f"{REDIS_KEY_PREFIX}:*"))
            if keys:
                return int(self.client.delete(*keys))
        except Exception:
            pass
        return 0


# ----------------------------------------------------------------------------
# Module-level singleton + runtime enable/disable toggle.
# ----------------------------------------------------------------------------
_cache_singleton: Optional[RedisCache] = None
_enabled: bool = REDIS_ENABLED


def get_cache() -> RedisCache:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = RedisCache()
    return _cache_singleton


def set_enabled(flag: bool) -> None:
    """Turn the cache on/off at runtime (used by the Streamlit toggle)."""
    global _enabled
    _enabled = bool(flag)


def is_enabled() -> bool:
    return _enabled


def cache_get(namespace: str, payload: Any) -> Optional[str]:
    if not _enabled:
        return None
    return get_cache().get(namespace, payload)


def cache_set(namespace: str, payload: Any, value: str) -> None:
    if not _enabled:
        return
    get_cache().set(namespace, payload, value)
