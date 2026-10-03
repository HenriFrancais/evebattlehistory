"""Stored fight-side / ship-count rollups (what the filters query) come from the
same side classification the UI shows — not from a killmail 2-colouring — and
follow FC/HC overrides."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import BrShipCount, FightKill, FightShipCount, FightSide
from tests.conftest import CREATOR_HEADERS
from tests.test_side_resolver import (
    HOSTILE,
    LONER,
    NV,
    _seed,
)


async def _seed_and_aggregate(session) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    from app.fights.aggregate import aggregate_br

    br_id, fight_id = await _seed(session)
    await aggregate_br(session, br_id=br_id, our_alliance_ids=[NV], our_corp_ids=[])
    await session.flush()
    return br_id, fight_id


async def _sides(session, fight_id: int) -> dict[str, FightSide]:  # type: ignore[no-untyped-def]
    rows = (await session.execute(select(FightSide).where(FightSide.fight_id == fight_id))).scalars()
    return {r.side_kind: r for r in rows}


async def test_three_way_brawl_is_not_collapsed_onto_one_side(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        _, fight_id = await _seed_and_aggregate(session)
        sides = await _sides(session, fight_id)
    assert set(sides) == {"friendly", "unassigned"}
    assert sides["friendly"].pilot_count == 1
    assert sides["friendly"].isk_lost == 5.0
    assert json.loads(sides["friendly"].alliance_ids_json) == [NV]
    # Everyone not classified stays out of "friendly": 4 distinct pilots, 117 ISK.
    assert sides["unassigned"].pilot_count == 4
    assert sides["unassigned"].isk_lost == 117.0


async def test_ship_counts_are_keyed_by_classified_side(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        br_id, fight_id = await _seed_and_aggregate(session)
        br_counts = {
            r.side_kind: r.count
            for r in (await session.execute(
                select(BrShipCount).where(BrShipCount.br_id == br_id)
            )).scalars()
        }
        sides = await _sides(session, fight_id)
        fight_counts = {
            r.side_idx: r.count
            for r in (await session.execute(
                select(FightShipCount).where(FightShipCount.fight_id == fight_id)
            )).scalars()
        }
    # One hull type in the fixture. NV pilot: 1 lost. Others: 3 lost.
    assert br_counts == {"friendly": 1, "unassigned": 3}
    assert fight_counts == {
        sides["friendly"].side_idx: 1,
        sides["unassigned"].side_idx: 3,
    }


async def test_victim_side_idx_matches_the_side_rows(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        _, fight_id = await _seed_and_aggregate(session)
        sides = await _sides(session, fight_id)
        fk = {
            r.killmail_id: r.side_idx
            for r in (await session.execute(
                select(FightKill).where(FightKill.fight_id == fight_id)
            )).scalars()
        }
    assert fk[3] == sides["friendly"].side_idx
    assert fk[1] == fk[2] == fk[4] == sides["unassigned"].side_idx


async def test_overrides_update_rollups_and_filters_agree_with_the_ui(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    async with get_sessionmaker(settings)() as session:
        br_id, fight_id = await _seed_and_aggregate(session)
        await session.commit()

    def hostile_ship_filter(client: TestClient) -> list[dict]:  # type: ignore[type-arg]
        r = client.post("/api/fights/filter", headers=CREATOR_HEADERS, json={
            "br_id": br_id,
            "tree": {"field": "ship_count", "ship": "TestShip", "op": ">=", "count": 1,
                     "side": "hostile"},
        })
        assert r.status_code == 200, r.text
        return r.json()

    with TestClient(create_app()) as client:
        assert hostile_ship_filter(client) == []  # nobody is marked hostile yet

        r = client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                       json={"entity_type": "alliance", "entity_id": HOSTILE, "side": "hostile"})
        assert r.status_code == 200
        r = client.put(f"/api/brs/{br_id}/participants/{LONER}/side",
                       headers=CREATOR_HEADERS, json={"side": "friendly"})
        assert r.status_code == 200

        hits = hostile_ship_filter(client)
        assert [f["fight_id"] for f in hits] == [fight_id]

        # The filter's sides are the same ones the BR page shows.
        ui = {s["side_kind"]: (s["pilot_count"], s["isk_lost"])
              for s in client.get(f"/api/brs/{br_id}/fights", headers=CREATOR_HEADERS).json()[0]["sides"]}
        flt = {s["side_kind"]: (s["pilot_count"], s["isk_lost"]) for s in hits[0]["sides"]}
        assert flt == ui
        assert ui == {"friendly": (2, 12.0), "hostile": (1, 10.0), "unassigned": (2, 100.0)}
    reset_engine_for_tests()


async def test_reaggregate_all_rebuilds_every_br(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import delete

    from app.fights.aggregate import reaggregate_all

    async with db_session_maker() as session:
        _, fight_id = await _seed_and_aggregate(session)
        await session.execute(delete(FightSide))  # simulate stale / legacy rollups
        assert await reaggregate_all(session, [NV], []) == 1
        assert set(await _sides(session, fight_id)) == {"friendly", "unassigned"}
