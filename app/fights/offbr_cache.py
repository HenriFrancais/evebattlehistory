"""Per-BR in-memory cache for off-BR participant derivation.

``offbr_log_characters`` re-derives a BR's off-BR participants from raw logs at
read time — several passes over every LogEvent stamped to the BR's fights plus a
Character lookup. It is called by BOTH the ``/composition`` and ``/sides``
endpoints, which fire together on every BR page open, so the (often multi-hundred
millisecond) computation was paid twice per load and re-paid on every re-render.

The result depends only on persisted killmail/log data and therefore changes ONLY
when the BR is (re)ingested. We cache the derived list per ``br_id`` and invalidate
it explicitly when ingest reaches "ready" (see app/ingest/pipeline). A per-BR lock
collapses the concurrent composition+sides cold-miss into a single computation; a
long TTL backstops staleness if an invalidation is ever missed (e.g. multi-worker).

Pure read cache: callers must treat the returned list as read-only (it is shared).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.fights.offbr_participants import OffBrChar, offbr_log_characters
from app.observability.logging import log

#: Backstop TTL (seconds). Explicit invalidation at ingest-ready is the primary
#: freshness mechanism; this only bounds staleness if an invalidation is missed.
_TTL_S = 900.0
#: Max distinct BRs held at once (LRU-evicted). Bounds memory on a long-lived process.
_MAX_ENTRIES = 128


class OffBrParticipantsCache:
    def __init__(self, ttl_s: float = _TTL_S, max_entries: int = _MAX_ENTRIES) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        # br_id -> (stored_at_monotonic, participants)
        self._entries: OrderedDict[str, tuple[float, list[OffBrChar]]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, br_id: str) -> asyncio.Lock:
        lock = self._locks.get(br_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[br_id] = lock
        return lock

    def _fresh(self, br_id: str) -> list[OffBrChar] | None:
        ent = self._entries.get(br_id)
        if ent is None:
            return None
        stored_at, data = ent
        if time.monotonic() - stored_at >= self._ttl:
            return None
        self._entries.move_to_end(br_id)  # LRU touch
        return data

    def _store(self, br_id: str, data: list[OffBrChar]) -> None:
        self._entries[br_id] = (time.monotonic(), data)
        self._entries.move_to_end(br_id)
        while len(self._entries) > self._max:
            evicted, _ = self._entries.popitem(last=False)
            self._locks.pop(evicted, None)

    async def get(
        self, session: AsyncSession, settings: Settings, br_id: str
    ) -> list[OffBrChar]:
        """Return the BR's off-BR participants, computing once on a cold miss.

        Concurrent callers for the same ``br_id`` share one computation via a
        per-BR lock; callers for different BRs never block each other.
        """
        hit = self._fresh(br_id)
        if hit is not None:
            return hit
        async with self._lock_for(br_id):
            hit = self._fresh(br_id)  # double-check after acquiring the lock
            if hit is not None:
                return hit
            data = await offbr_log_characters(session, settings, br_id)
            self._store(br_id, data)
            return data

    def invalidate(self, br_id: str) -> None:
        """Drop the cached entry for *br_id* (call when its data is rebuilt)."""
        if self._entries.pop(br_id, None) is not None:
            log.info("offbr_cache.invalidated", br_id=br_id)

    def clear(self) -> None:
        """Drop everything (test hook / full reset)."""
        self._entries.clear()
        self._locks.clear()


_singleton: OffBrParticipantsCache | None = None


def get_offbr_cache() -> OffBrParticipantsCache:
    global _singleton
    if _singleton is None:
        _singleton = OffBrParticipantsCache()
    return _singleton


def reset_offbr_cache_for_tests() -> None:
    """Drop the singleton so each test starts with an empty cache."""
    global _singleton
    _singleton = None
