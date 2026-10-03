"""If the NV Tools roster API is down at cold start, pages still load (without
roster-derived extras) instead of returning 500."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import CREATOR_HEADERS
from tests.test_read_perf import _boot


class _DownSource:
    name = "down"

    async def fetch_users(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("portal unreachable")


async def test_pages_load_without_the_roster(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.roster.source.factory as factory
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    br_id = await _boot(tmp_path, monkeypatch)
    monkeypatch.setattr(factory, "get_roster_source", lambda settings: _DownSource())
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        lst = client.get("/api/brs", headers=CREATOR_HEADERS)
        assert lst.status_code == 200, lst.text
        row = lst.json()["brs"][0]
        assert row["br_id"] == br_id
        assert row["friendly_pilots"] >= 1  # killmail-derived columns still work
        assert row["roster_present"] == 0

        cov = client.get(f"/api/brs/{br_id}/coverage", headers=CREATOR_HEADERS)
        assert cov.status_code == 200 and cov.json() == []

        parts = client.get(f"/api/brs/{br_id}/participants", headers=CREATOR_HEADERS)
        assert parts.status_code == 200
        assert all(p["user_name"] is None for p in parts.json())
    reset_roster_store_for_tests()


async def test_roster_recovery_invalidates_cached_reads(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api.derived_cache import get_derived_cache
    from app.config import get_settings
    from app.roster.snapshot import get_roster_store, reset_roster_store_for_tests

    reset_roster_store_for_tests()
    cache = get_derived_cache()
    n = {"v": 0}

    async def compute() -> int:
        n["v"] += 1
        return n["v"]

    assert await cache.get("k", compute) == 1
    await get_roster_store(get_settings()).get()  # first successful roster load
    assert await cache.get("k", compute) == 2
    reset_roster_store_for_tests()
