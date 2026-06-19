from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.db.models import (
    BattleReport,
    BrFight,
    FightKill,
    InventoryType,
    KillmailAttacker,
)
from tests.test_association import _insert_fight

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
VICTIM = 9001
ATTACKER = 9002
ABSOLUTION = 22428


async def _seed(session):  # type: ignore[no-untyped-def]
    # _insert_fight creates a Fight + Killmail (victim VICTIM, ship type 1 "TestShip")
    # + one KillmailAttacker (attacker_idx=0, character ATTACKER, NO ship) + Characters
    # named f"Char{id}".
    fight_id = await _insert_fight(session, victim_char_id=VICTIM, attacker_char_id=ATTACKER,
                                   started_at=FIGHT_START, ended_at=FIGHT_START)
    km_id = (
        await session.execute(select(FightKill.killmail_id).where(FightKill.fight_id == fight_id))
    ).scalar_one()
    # Give the existing attacker a real ship so composition can count it.
    await session.execute(
        update(KillmailAttacker)
        .where(KillmailAttacker.killmail_id == km_id, KillmailAttacker.character_id == ATTACKER)
        .values(ship_type_id=ABSOLUTION)
    )
    session.add(InventoryType(type_id=ABSOLUTION, name="Absolution"))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="demo", source_url="http://x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime.now(dt.UTC)))
    session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
    await session.flush()
    return br_id, fight_id


@pytest.mark.asyncio
async def test_composition_counts_ships_per_side(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    # The attacker flies an Absolution; everyone is unassigned (no baseline/override).
    side = next(s for s in result.sides if any(p.character_id == ATTACKER for p in s.pilots))
    ship = next(sh for sh in side.ships if sh.ship_type_id == ABSOLUTION)
    assert ship.count == 1
    assert ship.ship_name == "Absolution"
    pilot = next(p for p in side.pilots if p.character_id == ATTACKER)
    assert pilot.character_name == "Char9002"
    assert pilot.lost is False
    assert pilot.user_name is None  # char_to_user not provided


@pytest.mark.asyncio
async def test_composition_attaches_user_when_provided(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user={ATTACKER: "hfrench"},
        )

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert pilot.user_name == "hfrench"


@pytest.mark.asyncio
async def test_api_composition_contract(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app
    from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()
    get_app_config.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        member = client.get(f"/api/brs/{br_id}/composition", headers=MEMBER_HEADERS)
        creator = client.get(f"/api/brs/{br_id}/composition", headers=CREATOR_HEADERS)

    assert member.status_code == 200
    assert member.json()["by_user_available"] is False
    assert all(p["user_name"] is None for s in member.json()["sides"] for p in s["pilots"])
    assert creator.status_code == 200

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
