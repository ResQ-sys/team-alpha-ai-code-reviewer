"""In-process pub/sub :class:`EventBus` for live agent-activity streaming.

The review pipeline runs on a background thread while the HTTP layer streams
its progress to the browser over Server-Sent Events. This module is the bridge:
pipeline nodes :meth:`~EventBus.publish` events keyed by ``job_id``; the SSE
endpoint :meth:`~EventBus.subscribe` to receive a fresh queue that first
*replays* recent events (so a client that attaches slightly late still sees the
history) and then receives every subsequent event, terminated by a sentinel
when the job :meth:`~EventBus.close` s.

Design
------
* A single re-entrant lock guards all shared state, so the bus is safe to share
  across the FastAPI request threads and the pipeline worker thread.
* Per-job event buffers are **bounded** (:class:`collections.deque` with
  ``maxlen``) so a job whose stream is never drained cannot grow without limit.
* :data:`CLOSE` is a module-level sentinel; receiving it from a subscriber queue
  means "end of stream". It is intentionally identity-comparable (``is``).
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Set

__all__ = ["EventBus", "get_bus", "CLOSE"]

# Sentinel enqueued to every subscriber when a job closes. Subscribers detect
# end-of-stream with an identity check (`item is CLOSE`).
CLOSE: Any = object()

# Default cap on events retained per job for replay to late subscribers.
_DEFAULT_BUFFER_SIZE = 512

# Default cap on how many *closed* jobs the bus retains for replay. Once this many
# closed jobs have accumulated, the oldest ones are evicted (buffer + closed flag
# dropped) so a long-lived server does not leak one deque per job/run forever.
_DEFAULT_MAX_RETAINED_CLOSED = 256


class EventBus:
    """Thread-safe, in-process pub/sub broker keyed by ``job_id``.

    Each job has a bounded replay buffer and a list of live subscriber queues.
    """

    def __init__(
        self,
        buffer_size: int = _DEFAULT_BUFFER_SIZE,
        max_retained_closed: int = _DEFAULT_MAX_RETAINED_CLOSED,
    ) -> None:
        """Create an empty bus.

        Args:
            buffer_size: Maximum number of recent events retained per job for
                replay to subscribers that attach after publishing has begun.
            max_retained_closed: Maximum number of *closed* jobs whose state is
                kept for replay. Older closed jobs are evicted once this many
                accumulate, bounding memory on a long-lived server.
        """
        self._buffer_size = max(1, int(buffer_size))
        self._max_retained_closed = max(1, int(max_retained_closed))
        self._lock = threading.RLock()
        self._buffers: Dict[str, Deque[dict]] = {}
        self._subscribers: Dict[str, List["queue.Queue"]] = {}
        self._closed: Set[str] = set()
        # Closed job ids in close order (oldest first), for LRU-style eviction.
        self._closed_order: Deque[str] = deque()

    def publish(self, job_id: str, event: dict) -> None:
        """Record ``event`` for ``job_id`` and fan it out to live subscribers.

        The event is appended to the (bounded) replay buffer first, then pushed
        to every currently-subscribed queue. Safe to call from any thread.
        """
        with self._lock:
            buf = self._buffers.get(job_id)
            if buf is None:
                buf = deque(maxlen=self._buffer_size)
                self._buffers[job_id] = buf
            buf.append(event)
            for subscriber in list(self._subscribers.get(job_id, ())):
                subscriber.put(event)

    def subscribe(self, job_id: str) -> "queue.Queue":
        """Register a fresh subscriber queue for ``job_id`` and return it.

        The returned queue is pre-loaded with all buffered (already-published)
        events so a late subscriber still receives the history, then receives
        every subsequent event. If the job is already closed, the queue also
        receives the :data:`CLOSE` sentinel immediately so the consumer
        terminates without hanging.
        """
        subscriber: "queue.Queue" = queue.Queue()
        with self._lock:
            for event in self._buffers.get(job_id, ()):  # replay history
                subscriber.put(event)
            self._subscribers.setdefault(job_id, []).append(subscriber)
            if job_id in self._closed:
                subscriber.put(CLOSE)
        return subscriber

    def unsubscribe(self, job_id: str, subscriber: "queue.Queue") -> None:
        """Detach ``subscriber`` from ``job_id`` (idempotent).

        Consumers should call this (e.g. in a ``finally``) so completed streams
        do not leak subscriber references on a long-lived server.
        """
        with self._lock:
            subs = self._subscribers.get(job_id)
            if not subs:
                return
            try:
                subs.remove(subscriber)
            except ValueError:
                return
            if not subs:
                self._subscribers.pop(job_id, None)

    def close(self, job_id: str) -> None:
        """Signal end-of-stream for ``job_id`` to all current subscribers.

        Marks the job closed (so future subscribers terminate after replay) and
        pushes the :data:`CLOSE` sentinel to every live subscriber queue.
        """
        with self._lock:
            if job_id not in self._closed:
                self._closed.add(job_id)
                self._closed_order.append(job_id)
            for subscriber in list(self._subscribers.get(job_id, ())):
                subscriber.put(CLOSE)
            self._evict_closed()

    def _evict_closed(self) -> None:
        """Drop the oldest closed jobs once the retention cap is exceeded.

        Bounds process memory: without this, every finished job/run permanently
        leaks a replay buffer + closed flag. Active subscribers are unaffected —
        they already captured the replayed history on subscribe and hold their
        own queue — so only the replay buffer for future subscribers is dropped.
        Caller must hold ``self._lock``.
        """
        while len(self._closed_order) > self._max_retained_closed:
            old = self._closed_order.popleft()
            self._buffers.pop(old, None)
            self._closed.discard(old)
            # Leave any live subscriber list in place; unsubscribe cleans it up.

    def reset(self, job_id: str) -> None:
        """Drop all state for ``job_id`` (buffer, subscribers, closed flag).

        Primarily for tests; not called on the normal completion path so a
        reconnecting client can still replay a finished job's history.
        """
        with self._lock:
            self._buffers.pop(job_id, None)
            self._subscribers.pop(job_id, None)
            self._closed.discard(job_id)
            try:
                self._closed_order.remove(job_id)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
_BUS_SINGLETON: "EventBus | None" = None
_BUS_SINGLETON_LOCK = threading.Lock()


def get_bus() -> EventBus:
    """Return the process-wide :class:`EventBus` singleton (lazily created)."""
    global _BUS_SINGLETON
    if _BUS_SINGLETON is None:
        with _BUS_SINGLETON_LOCK:
            if _BUS_SINGLETON is None:
                _BUS_SINGLETON = EventBus()
    return _BUS_SINGLETON
