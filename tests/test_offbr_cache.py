"""Per-BR off-BR participant cache: correctness (matches direct computation),
cache-hit avoidance of recompute, and explicit invalidation."""
from __future__ import annotations

import pytest

from tests.test_offbr_participants import _seed_offbr_br


async def test_cache_matches_direct_computation(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """The cached list is identical to calling offbr_log_characters directly."""
    from app.config import get_settings
    from app.fights.offbr_cache import OffBrParticipantsCache
    from app.fights.offbr_participants import offbr_log_characters

    settings = get_settings()
    async with db_session_maker() as session:
        br_id = await _seed_offbr_br(session)
        await session.commit()

    async with db_session_maker() as session:
        direct = await offbr_log_characters(session, settings, br_id)
        cache = OffBrParticipantsCache()
        cached = await cache.get(session, settings, br_id)

    assert [c.character_id for c in cached] == [c.character_id for c in direct]
    assert cached == direct  # OffBrChar is a dataclass → value equality


async def test_cache_hit_skips_recompute_until_invalidated(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Second get() for the same BR is served from cache (no recompute); after
    invalidate() it recomputes."""
    from app.config import get_settings
    import app.fights.offbr_cache as cache_mod

    settings = get_settings()
    async with db_session_maker() as session:
        br_id = await _seed_offbr_br(session)
        await session.commit()

    calls = {"n": 0}
    real = cache_mod.offbr_log_characters

    async def counting(session, settings, br_id):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return await real(session, settings, br_id)

    monkeypatch.setattr(cache_mod, "offbr_log_characters", counting)

    cache = cache_mod.OffBrParticipantsCache()
    async with db_session_maker() as session:
        await cache.get(session, settings, br_id)
        await cache.get(session, settings, br_id)
        await cache.get(session, settings, br_id)
    assert calls["n"] == 1, "cache should compute once, then serve hits"

    cache.invalidate(br_id)
    async with db_session_maker() as session:
        await cache.get(session, settings, br_id)
    assert calls["n"] == 2, "invalidate() forces a recompute"


async def test_cache_ttl_backstop_expires(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An entry older than the TTL is recomputed even without explicit invalidation."""
    from app.config import get_settings
    import app.fights.offbr_cache as cache_mod

    settings = get_settings()
    async with db_session_maker() as session:
        br_id = await _seed_offbr_br(session)
        await session.commit()

    calls = {"n": 0}
    real = cache_mod.offbr_log_characters

    async def counting(session, settings, br_id):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return await real(session, settings, br_id)

    monkeypatch.setattr(cache_mod, "offbr_log_characters", counting)

    cache = cache_mod.OffBrParticipantsCache(ttl_s=0.0)  # everything is immediately stale
    async with db_session_maker() as session:
        await cache.get(session, settings, br_id)
        await cache.get(session, settings, br_id)
    assert calls["n"] == 2, "TTL=0 means each get recomputes"


def test_singleton_is_stable() -> None:
    from app.fights.offbr_cache import get_offbr_cache

    assert get_offbr_cache() is get_offbr_cache()
