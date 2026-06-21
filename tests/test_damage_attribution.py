"""TDD tests for Task 15: per-loss damage attribution analytics + API endpoint.

Analytics contract:
  loss_damage_attribution(session, killmail_id) -> LossDamageAttribution
  - attackers sorted by damage_done desc
  - share = damage_done / total_attributed (0.0 if total is 0)
  - final_blow flag passed through per attacker
  - damage_taken from Killmail.damage_taken

API contract:
  GET /api/brs/{br_id}/losses/{killmail_id}/damage
  - 200 with LossDamageAttributionOut shape when killmail is in the BR
  - 404 when killmail is not linked to the given BR (guard via BR↔fight↔killmail join)
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    BattleReport,
    Character,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
    SolarSystem,
)
from tests.conftest import MEMBER_HEADERS, TEST_TOKEN
from tests.test_e3_fleet_timeline import _make_br_with_fight

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_SOLAR_SYSTEM_ID = 31002222
_SHIP_TYPE_ID = 645  # Merlin (arbitrary, just must satisfy FK)


async def _ensure_prereqs(session) -> None:  # type: ignore[no-untyped-def]
    """Ensure SolarSystem + InventoryType rows needed by Killmail FKs exist."""
    if not (
        await session.execute(
            select(SolarSystem).where(SolarSystem.system_id == _SOLAR_SYSTEM_ID)
        )
    ).scalar_one_or_none():
        session.add(SolarSystem(system_id=_SOLAR_SYSTEM_ID, name="J-Test", security=None))
        await session.flush()

    if not (
        await session.execute(
            select(InventoryType).where(InventoryType.type_id == _SHIP_TYPE_ID)
        )
    ).scalar_one_or_none():
        session.add(InventoryType(type_id=_SHIP_TYPE_ID, name="Merlin"))
        await session.flush()


async def _ensure_character(session, char_id: int, name: str) -> None:  # type: ignore[no-untyped-def]
    if not (
        await session.execute(select(Character).where(Character.character_id == char_id))
    ).scalar_one_or_none():
        session.add(Character(
            character_id=char_id,
            name=name,
            last_seen_at=dt.datetime.now(dt.UTC),
        ))
        await session.flush()


async def _seed_loss(session, fight_id: int, km_id: int = 900) -> int:  # type: ignore[no-untyped-def]
    """Seed a Killmail + two KillmailAttacker rows + a FightKill link."""
    await _ensure_prereqs(session)
    await _ensure_character(session, 10, "AttackerAlpha")
    await _ensure_character(session, 11, "AttackerBeta")

    session.add(Killmail(
        killmail_id=km_id,
        killmail_time=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
        solar_system_id=_SOLAR_SYSTEM_ID,
        victim_character_id=None,
        victim_ship_type_id=_SHIP_TYPE_ID,
        total_value=1.0,
        damage_taken=4000,
        npc_kill=False,
        solo_kill=False,
    ))
    session.add(KillmailAttacker(
        killmail_id=km_id,
        attacker_idx=0,
        character_id=10,
        ship_type_id=_SHIP_TYPE_ID,
        damage_done=3000,
        final_blow=False,
    ))
    session.add(KillmailAttacker(
        killmail_id=km_id,
        attacker_idx=1,
        character_id=11,
        ship_type_id=_SHIP_TYPE_ID,
        damage_done=1000,
        final_blow=True,
    ))
    session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
    await session.flush()
    return km_id


# ---------------------------------------------------------------------------
# Analytics tests (Step 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranked_with_share_and_final_blow(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """loss_damage_attribution returns attackers sorted by damage_done desc with correct share."""
    from app.analytics.damage_attribution import loss_damage_attribution

    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_loss(s, fight_id)
        await s.commit()

    async with db_session_maker() as s:
        res = await loss_damage_attribution(s, km_id)

    assert [r.damage_done for r in res.attackers] == [3000, 1000]
    assert res.total_attributed == 4000
    assert res.damage_taken == 4000
    assert abs(res.attackers[0].share - 0.75) < 1e-6
    assert abs(res.attackers[1].share - 0.25) < 1e-6
    final_blow_row = next(r for r in res.attackers if r.damage_done == 1000)
    assert final_blow_row.final_blow is True
    assert res.attackers[0].final_blow is False


@pytest.mark.asyncio
async def test_character_names_resolved(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """loss_damage_attribution resolves character names from the Character table."""
    from app.analytics.damage_attribution import loss_damage_attribution

    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_loss(s, fight_id, km_id=901)
        await s.commit()

    async with db_session_maker() as s:
        res = await loss_damage_attribution(s, km_id)

    names = {r.character_id: r.character_name for r in res.attackers}
    assert names[10] == "AttackerAlpha"
    assert names[11] == "AttackerBeta"


@pytest.mark.asyncio
async def test_zero_total_gives_zero_share(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """When total_attributed == 0 all share values are 0.0 (not divide-by-zero)."""
    from app.analytics.damage_attribution import loss_damage_attribution

    async with db_session_maker() as s:
        await _ensure_prereqs(s)
        # Killmail with no attackers (or NPC with 0 damage done rows)
        km_id = 902
        s.add(Killmail(
            killmail_id=km_id,
            killmail_time=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
            solar_system_id=_SOLAR_SYSTEM_ID,
            victim_character_id=None,
            victim_ship_type_id=_SHIP_TYPE_ID,
            total_value=1.0,
            damage_taken=None,
            npc_kill=False,
            solo_kill=False,
        ))
        s.add(KillmailAttacker(
            killmail_id=km_id, attacker_idx=0,
            character_id=None, damage_done=0, final_blow=True,
        ))
        await s.flush()
        await s.commit()

    async with db_session_maker() as s:
        res = await loss_damage_attribution(s, km_id)

    assert res.total_attributed == 0
    assert res.damage_taken is None
    for r in res.attackers:
        assert r.share == 0.0


# ---------------------------------------------------------------------------
# API tests (Step 5) — require the endpoint implemented to go GREEN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_damage_endpoint_returns_attribution_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/losses/{km_id}/damage returns the attribution JSON."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)

    async with sm() as s:
        br_id, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_loss(s, fight_id, km_id=910)
        await s.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        r = client.get(f"/api/brs/{br_id}/losses/{km_id}/damage", headers=MEMBER_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["killmail_id"] == km_id
        assert body["damage_taken"] == 4000
        assert body["total_attributed"] == 4000
        attackers = body["attackers"]
        assert len(attackers) == 2
        # sorted desc by damage_done
        assert attackers[0]["damage_done"] == 3000
        assert attackers[1]["damage_done"] == 1000
        assert abs(attackers[0]["share"] - 0.75) < 1e-6
        assert attackers[1]["final_blow"] is True
        assert attackers[0]["final_blow"] is False


@pytest.mark.asyncio
async def test_damage_endpoint_404_killmail_not_in_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET .../losses/{km_id}/damage → 404 when the killmail is not in that BR."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)

    async with sm() as s:
        # BR A with km 920 linked to it
        br_id_a, fight_id_a = await _make_br_with_fight(s)
        km_id = await _seed_loss(s, fight_id_a, km_id=920)
        # BR B with no kills at all
        br_id_b = str(uuid.uuid4())
        s.add(BattleReport(
            br_id=br_id_b,
            source="demo",
            source_url="http://x",
            source_ref="ref",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        ))
        await s.flush()
        await s.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # km_id belongs to br_id_a, NOT br_id_b → should 404
        r = client.get(
            f"/api/brs/{br_id_b}/losses/{km_id}/damage",
            headers=MEMBER_HEADERS,
        )
        assert r.status_code == 404

        # Sanity: the correct BR returns 200
        r2 = client.get(
            f"/api/brs/{br_id_a}/losses/{km_id}/damage",
            headers=MEMBER_HEADERS,
        )
        assert r2.status_code == 200
