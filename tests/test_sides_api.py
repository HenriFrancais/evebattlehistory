"""API tests for per-BR side overrides: gating + override changes classification."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    Alliance,
    BattleReport,
    BrFight,
    Corporation,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    SolarSystem,
)
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

ENEMY_ALLI = 99099099  # not a baseline blue


async def _seed(session) -> str:  # type: ignore[no-untyped-def]
    now = dt.datetime.now(dt.UTC)
    sid = 31009999
    session.add(SolarSystem(system_id=sid, name="J-SIDES", security=None))
    session.add(Alliance(alliance_id=ENEMY_ALLI, name="Enemy Alliance", last_seen_at=now))
    session.add(Corporation(corporation_id=500, name="Enemy Corp",
                            alliance_id=ENEMY_ALLI, last_seen_at=now))
    session.add(InventoryType(type_id=1, name="TestShip"))
    await session.flush()
    fight = Fight(system_id=sid, started_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
                  ended_at=dt.datetime(2026, 6, 1, 0, 5, tzinfo=dt.UTC),
                  isk_destroyed_total=0.0, largest_side_pilots=1,
                  capitals_involved=False, distinct_alliance_count=1)
    session.add(fight)
    await session.flush()
    km_id = 7777777
    session.add(Killmail(killmail_id=km_id, killmail_time=dt.datetime(2026, 6, 1, 0, 1, tzinfo=dt.UTC),
                         solar_system_id=sid, victim_character_id=None, victim_corporation_id=500,
                         victim_alliance_id=ENEMY_ALLI, victim_ship_type_id=1, total_value=1.0,
                         npc_kill=False, solo_kill=False))
    await session.flush()
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=km_id, side_idx=0))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="demo", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime.now(dt.UTC)))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.flush()
    return br_id


@pytest.mark.asyncio
async def test_sides_get_and_override_flow(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        br_id = await _seed(session)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # GET: a non-blue alliance defaults to unassigned.
        r = client.get(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS)
        assert r.status_code == 200
        ents = {e["entity_id"]: e for e in r.json()["entities"]}
        assert ents[ENEMY_ALLI]["side"] == "unassigned"
        assert r.json()["can_edit"] is True

        # Member cannot edit.
        rm = client.put(f"/api/brs/{br_id}/sides", headers=MEMBER_HEADERS,
                        json={"entity_type": "alliance", "entity_id": ENEMY_ALLI, "side": "friendly"})
        assert rm.status_code == 403

        # FC overrides it friendly → reflected in response.
        rp = client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                        json={"entity_type": "alliance", "entity_id": ENEMY_ALLI, "side": "friendly"})
        assert rp.status_code == 200
        ents = {e["entity_id"]: e for e in rp.json()["entities"]}
        assert ents[ENEMY_ALLI]["side"] == "friendly"
        assert ents[ENEMY_ALLI]["overridden"] is True

        # Kill marker now classifies that victim friendly too.
        ft = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=CREATOR_HEADERS).json()
        assert ft["kills"][0]["side_kind"] == "friendly"

        # Clear override → back to unassigned.
        rc = client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                        json={"entity_type": "alliance", "entity_id": ENEMY_ALLI, "side": None})
        ents = {e["entity_id"]: e for e in rc.json()["entities"]}
        assert ents[ENEMY_ALLI]["side"] == "unassigned"
        assert ents[ENEMY_ALLI]["overridden"] is False

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_sides_override_updates_br_summary(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Moving an entity between sides re-derives the BR headline ISK / result."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        br_id = await _seed(session)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # The single kill is an enemy loss worth 1.0 ISK.
        # Mark the alliance hostile → we destroyed it.
        client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                   json={"entity_type": "alliance", "entity_id": ENEMY_ALLI, "side": "hostile"})
        br = client.get(f"/api/brs/{br_id}").json()
        assert br["our_isk_destroyed"] == pytest.approx(1.0)
        assert br["our_isk_lost"] == pytest.approx(0.0)
        assert br["isk_efficiency"] == pytest.approx(1.0)
        assert br["result"] == "win"

        # Move it to friendly → that ISK now counts as our loss.
        client.put(f"/api/brs/{br_id}/sides", headers=CREATOR_HEADERS,
                   json={"entity_type": "alliance", "entity_id": ENEMY_ALLI, "side": "friendly"})
        br = client.get(f"/api/brs/{br_id}").json()
        assert br["our_isk_destroyed"] == pytest.approx(0.0)
        assert br["our_isk_lost"] == pytest.approx(1.0)
        assert br["isk_efficiency"] == pytest.approx(0.0)
        assert br["result"] == "loss"

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()
