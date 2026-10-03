"""Heavy reads: responses are compressed, the BR list is paged, and derived
results are cached until something that feeds them changes."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.db.models import BattleReport
from tests.conftest import CREATOR_HEADERS
from tests.test_classified_rollups import _seed_and_aggregate
from tests.test_side_resolver import HOSTILE


async def _boot(tmp_path, monkeypatch, extra_brs: int = 0):  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    async with get_sessionmaker(settings)() as session:
        br_id, _ = await _seed_and_aggregate(session)
        for i in range(extra_brs):
            session.add(BattleReport(
                br_id=f"extra-{i:03d}", source="demo", source_url="x", source_ref="r",
                title=f"Battle number {i} with a reasonably long descriptive title",
                created_by_user="t", status="ready", progress_pct=100,
                created_at=dt.datetime(2026, 1, 1) + dt.timedelta(days=i),
                battle_at=dt.datetime(2026, 1, 1) + dt.timedelta(days=i),
                result="win", our_isk_destroyed=10.0, our_isk_lost=1.0,
            ))
        await session.commit()
    return br_id


async def test_large_json_responses_are_gzipped(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    await _boot(tmp_path, monkeypatch, extra_brs=40)
    with TestClient(create_app()) as client:
        r = client.get("/api/brs?limit=200", headers=CREATOR_HEADERS)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert len(r.json()["brs"]) == 41


async def test_br_list_is_paged_but_summarises_everything(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    seeded = await _boot(tmp_path, monkeypatch, extra_brs=7)
    with TestClient(create_app()) as client:
        page1 = client.get("/api/brs?limit=5", headers=CREATOR_HEADERS).json()
        page2 = client.get("/api/brs?limit=5&offset=5", headers=CREATOR_HEADERS).json()
    assert page1["total"] == page2["total"] == 8
    assert len(page1["brs"]) == 5 and len(page2["brs"]) == 3
    assert page1["summary"]["total"] == 8  # the win-rate summary covers ALL reports
    ids = [b["br_id"] for b in page1["brs"] + page2["brs"]]
    assert len(set(ids)) == 8
    # Newest battle first: the seeded June fight, then the January extras descending.
    assert ids[:3] == [seeded, "extra-006", "extra-005"]


async def test_fleet_timeline_is_cached_until_sides_change(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.fleet as fleet_api
    from app.main import create_app

    br_id = await _boot(tmp_path, monkeypatch)
    calls: list[str] = []
    real = fleet_api.fleet_timeline

    async def spy(session, br, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append(br)
        return await real(session, br, *a, **kw)

    monkeypatch.setattr(fleet_api, "fleet_timeline", spy)
    url = f"/api/brs/{br_id}/fleet-timeline"
    with TestClient(create_app()) as client:
        first = client.get(url, headers=CREATOR_HEADERS).json()
        again = client.get(url, headers=CREATOR_HEADERS).json()
        assert again == first
        assert len(calls) == 1, "second read should be served from cache"

        client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                   json={"entity_type": "alliance", "entity_id": HOSTILE, "side": "hostile"})
        changed = client.get(url, headers=CREATOR_HEADERS).json()
        assert len(calls) == 2, "a side change must invalidate the cache"
    sides = {k["killmail_id"]: k["side_kind"] for k in changed["kills"]}
    assert sides[1] == "hostile"


def test_derived_cache_version_bump_and_ttl() -> None:
    import asyncio

    from app.api.derived_cache import DerivedCache

    cache = DerivedCache(ttl_s=1000.0)
    n = {"v": 0}

    async def compute() -> int:
        n["v"] += 1
        return n["v"]

    async def run() -> list[int]:
        out = [await cache.get("k", compute), await cache.get("k", compute)]
        cache.bump()
        out.append(await cache.get("k", compute))
        cache.ttl_s = 0.0  # expired
        out.append(await cache.get("k", compute))
        return out

    assert asyncio.run(run()) == [1, 1, 2, 3]


async def test_composition_is_cached_per_audience(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.api.fleet as fleet_api
    from app.main import create_app
    from tests.conftest import MEMBER_HEADERS

    br_id = await _boot(tmp_path, monkeypatch)
    calls: list[bool] = []
    real = fleet_api.fleet_composition

    async def spy(session, br, **kw):  # type: ignore[no-untyped-def]
        calls.append(kw.get("char_to_user") is not None)
        return await real(session, br, **kw)

    monkeypatch.setattr(fleet_api, "fleet_composition", spy)
    url = f"/api/brs/{br_id}/composition"
    with TestClient(create_app()) as client:
        fc1 = client.get(url, headers=CREATOR_HEADERS).json()
        fc2 = client.get(url, headers=CREATOR_HEADERS).json()
        member = client.get(url, headers=MEMBER_HEADERS).json()
        client.get(url, headers=MEMBER_HEADERS)
    assert fc1 == fc2
    # One computation with the user mapping (FC/HC) and one without (members).
    assert calls == [True, False]
    assert fc1["by_user_available"] is True and member["by_user_available"] is False


async def test_pre_migration_snapshot_runs_off_the_event_loop(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import sqlite3
    import threading

    import app.db.engine as engine_mod
    from app.config import get_settings
    from app.db.engine import init_models, reset_engine_for_tests

    db = tmp_path / "old.db"
    monkeypatch.setenv("DB_PATH", str(db))
    get_settings.cache_clear()
    reset_engine_for_tests()
    await init_models(get_settings())
    reset_engine_for_tests()
    with sqlite3.connect(db) as c:
        c.execute("pragma user_version = 1")

    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = engine_mod.snapshot_before_migrate

    def spy(path, version):  # type: ignore[no-untyped-def]
        seen.append(threading.get_ident())
        return real(path, version)

    monkeypatch.setattr(engine_mod, "snapshot_before_migrate", spy)
    await init_models(get_settings())
    reset_engine_for_tests()
    assert seen and seen[0] != loop_thread
