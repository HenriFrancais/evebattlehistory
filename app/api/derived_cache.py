"""Process-wide cache for expensive derived read models.

The fleet timeline and the BR-list enrichment are pure functions of persisted
killmail / log / side data, cost hundreds of milliseconds of Python on the event
loop, and are requested repeatedly (the SPA even prefetches them on hover).

Invalidation is deliberately coarse: ONE global version. Any write that can
change a derived result calls ``bump_derived()`` and every cached entry becomes
stale at once — there is no per-key bookkeeping to get wrong. A TTL backstops
writes made by another process (the maintenance CLIs: reparse, re-aggregate).

Correct only with a single app worker (see deploy/Dockerfile).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


class DerivedCache:
    def __init__(self, ttl_s: float = 300.0, max_entries: int = 256) -> None:
        self.ttl_s = ttl_s
        self._max = max_entries
        self._version = 0
        # key -> (version, stored_at_monotonic, value)
        self._entries: OrderedDict[Hashable, tuple[int, float, Any]] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def bump(self) -> None:
        """Invalidate everything (call after any write that feeds a derived read)."""
        self._version += 1

    def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()

    def _fresh(self, key: Hashable) -> tuple[bool, Any]:
        ent = self._entries.get(key)
        if ent is None:
            return False, None
        version, stored_at, value = ent
        if version != self._version or time.monotonic() - stored_at >= self.ttl_s:
            return False, None
        self._entries.move_to_end(key)
        return True, value

    async def get(self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value for *key*, computing it once on a miss.

        Concurrent callers for the same key share one computation.
        """
        hit, value = self._fresh(key)
        if hit:
            return value  # type: ignore[no-any-return]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit, value = self._fresh(key)
            if hit:
                return value  # type: ignore[no-any-return]
            version = self._version
            result = await compute()
            self._entries[key] = (version, time.monotonic(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                evicted, _ = self._entries.popitem(last=False)
                self._locks.pop(evicted, None)
            return result


_cache = DerivedCache()


def get_derived_cache() -> DerivedCache:
    return _cache


def bump_derived() -> None:
    _cache.bump()


def reset_derived_cache_for_tests() -> None:
    global _cache
    _cache = DerivedCache()
