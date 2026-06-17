"""Stale-while-revalidate snapshot of the NV Tools roster (/api/users).

Same shape as nvskills' SnapshotStore: fresh → serve; stale → serve + refresh in
background; cold → block. Drives create-permission display, coverage joins, and
Listener-name → character resolution for logs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.config import Settings
from app.observability.health import HEALTH
from app.observability.logging import log
from app.roster.models import RosterCharacter, RosterUser
from app.roster.source.base import RosterSource


@dataclass(frozen=True)
class RosterSnapshot:
    users: list[RosterUser]
    version: int
    fetched_at: float
    # character_id → owning user_name
    char_to_user: dict[int, str] = field(default_factory=dict)
    # lowercased character_name → character_id
    name_to_char_id: dict[str, int] = field(default_factory=dict)
    # user_name → list of characters
    user_to_chars: dict[str, list[RosterCharacter]] = field(default_factory=dict)


def build_roster_snapshot(
    users: list[RosterUser], version: int, fetched_at: float
) -> RosterSnapshot:
    char_to_user: dict[int, str] = {}
    name_to_char_id: dict[str, int] = {}
    user_to_chars: dict[str, list[RosterCharacter]] = {}
    for u in users:
        user_to_chars[u.user_name] = list(u.characters)
        for c in u.characters:
            char_to_user[c.character_id] = u.user_name
            name_to_char_id[c.character_name.strip().lower()] = c.character_id
    return RosterSnapshot(
        users=users,
        version=version,
        fetched_at=fetched_at,
        char_to_user=char_to_user,
        name_to_char_id=name_to_char_id,
        user_to_chars=user_to_chars,
    )


class RosterStore:
    def __init__(self, settings: Settings, source: RosterSource) -> None:
        self._settings = settings
        self._source = source
        self._state: RosterSnapshot | None = None
        self._version = 0
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[RosterSnapshot] | None = None

    @property
    def has_state(self) -> bool:
        return self._state is not None

    async def get(self) -> RosterSnapshot:
        now = time.monotonic()
        if self._state is not None and now < self._expires_at:
            return self._state
        if self._state is not None:
            self._schedule_refresh()
            return self._state
        return await self._cold_fetch()

    async def _cold_fetch(self) -> RosterSnapshot:
        async with self._lock:
            if self._state is not None:
                return self._state
            snap = await self._fetch()
            self._set_state(snap)
            return snap

    def _schedule_refresh(self) -> None:
        if self._inflight is not None and not self._inflight.done():
            return
        self._inflight = asyncio.create_task(self._background_refresh())

    async def _background_refresh(self) -> RosterSnapshot:
        try:
            snap = await self._fetch()
            self._set_state(snap)
            return snap
        except Exception as exc:
            log.warning("roster.refresh_failed", error=str(exc))
            assert self._state is not None
            self._expires_at = time.monotonic() + self._settings.roster_ttl_s
            return self._state

    def _set_state(self, snap: RosterSnapshot) -> None:
        self._state = snap
        self._version = snap.version
        self._expires_at = time.monotonic() + self._settings.roster_ttl_s
        HEALTH.roster_loaded = True
        HEALTH.roster_version = snap.version
        HEALTH.roster_fetched_at = snap.fetched_at

    async def _fetch(self) -> RosterSnapshot:
        payload = await self._source.fetch_users()
        snap = build_roster_snapshot(payload.users, self._version + 1, time.time())
        log.info("roster.fetched", version=snap.version, users=len(snap.users))
        return snap


_singleton: RosterStore | None = None


def get_roster_store(settings: Settings) -> RosterStore:
    global _singleton
    if _singleton is None:
        from app.roster.source.factory import get_roster_source

        _singleton = RosterStore(settings, get_roster_source(settings))
    return _singleton


def reset_roster_store_for_tests() -> None:
    global _singleton
    _singleton = None
