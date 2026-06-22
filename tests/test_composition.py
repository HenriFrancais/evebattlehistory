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


CAPSULE = 670
GUARDIAN = 11987


@pytest.mark.asyncio
async def test_composition_excludes_capsules_and_flags_reships(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings
    from app.db.models import FightKill, InventoryType, Killmail, KillmailAttacker

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # ATTACKER flies Absolution (attacker_idx 0)
        session.add(InventoryType(type_id=GUARDIAN, name="Guardian"))
        session.add(InventoryType(type_id=CAPSULE, name="Capsule"))
        km_id = (await session.execute(
            select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
        )).scalar_one()
        # Reship: same ATTACKER also appears in a Guardian on the same killmail.
        session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=1, character_id=ATTACKER,
                                     ship_type_id=GUARDIAN, damage_done=1, final_blow=False))
        # A capsule victim for ATTACKER (podded) must NOT add a Capsule hull.
        session.add(Killmail(killmail_id=km_id + 1,
                             killmail_time=dt.datetime(2026, 6, 10, 20, 1, tzinfo=dt.UTC),
                             solar_system_id=31002222, victim_character_id=ATTACKER,
                             victim_ship_type_id=CAPSULE, npc_kill=False, solo_kill=False))
        session.add(FightKill(fight_id=fight_id, killmail_id=km_id + 1, side_idx=0))
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    pilots = [p for s in result.sides for p in s.pilots]
    hulls = {p.ship_name for p in pilots if p.character_id == ATTACKER}
    assert hulls == {"Absolution", "Guardian"}            # both hulls, capsule excluded
    assert all(p.reship for p in pilots if p.character_id == ATTACKER)
    assert not any(p.ship_name == "Capsule" for p in pilots)
    # ATTACKER counted once toward pilot_count despite two hulls
    side = next(s for s in result.sides if any(p.character_id == ATTACKER for p in s.pilots))
    assert sum(1 for p in side.pilots if p.character_id == ATTACKER) == 2  # two hull rows
    assert side.pilot_count == len({p.character_id for p in side.pilots})


@pytest.mark.asyncio
async def test_composition_lost_hull_has_killmail_id(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # VICTIM lost ship type 1 ("TestShip") on a km
        km_id = (await session.execute(
            select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
        )).scalar_one()
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    victim = next(p for s in result.sides for p in s.pilots if p.character_id == VICTIM)
    assert victim.lost is True and victim.killmail_id == km_id
    attacker = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert attacker.killmail_id is None  # not lost


RAILGUN_ID = 3074
RAILGUN_NAME = "Electron Blaster Cannon I"


@pytest.mark.asyncio
async def test_composition_pilot_weapons_from_attacker(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Pilot row carries weapons list derived from weapon_type_id on KillmailAttacker."""
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)
        km_id = (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
            )
        ).scalar_one()
        # Give the attacker a weapon_type_id and seed its InventoryType (category 6 = turret)
        session.add(InventoryType(
            type_id=RAILGUN_ID, name=RAILGUN_NAME, group_name="Hybrid Weapon", category_id=6,
        ))
        await session.execute(
            update(KillmailAttacker)
            .where(KillmailAttacker.killmail_id == km_id, KillmailAttacker.character_id == ATTACKER)
            .values(weapon_type_id=RAILGUN_ID)
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert len(pilot.weapons) == 1
    w = pilot.weapons[0]
    assert w.type_id == RAILGUN_ID
    assert w.name == RAILGUN_NAME
    assert w.role == "turret"


@pytest.mark.asyncio
async def test_composition_ship_top_modules(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """CompositionShip.top_modules lists the hull's modules (most common first, ≤5)."""
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # ATTACKER flies Absolution
        km_id = (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
            )
        ).scalar_one()
        session.add(InventoryType(
            type_id=RAILGUN_ID, name=RAILGUN_NAME, group_name="Hybrid Weapon", category_id=6,
        ))
        await session.execute(
            update(KillmailAttacker)
            .where(KillmailAttacker.killmail_id == km_id, KillmailAttacker.character_id == ATTACKER)
            .values(weapon_type_id=RAILGUN_ID)
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    absol = next(sh for s in result.sides for sh in s.ships if sh.ship_name == "Absolution")
    assert len(absol.top_modules) <= 5
    assert any(m.type_id == RAILGUN_ID and m.name == RAILGUN_NAME for m in absol.top_modules)


@pytest.mark.asyncio
async def test_composition_pilot_weapons_none_weapon_type_id(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Pilot with no weapon_type_id has an empty weapons list (no crash)."""
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

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert pilot.weapons == []


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
    assert all("weapons" in p for s in member.json()["sides"] for p in s["pilots"])
    assert creator.status_code == 200

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# FIX 3: capsule weapon_type_id must not produce a weapon chip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composition_capsule_weapon_type_id_produces_no_weapon(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """An attacker row with weapon_type_id == CAPSULE_TYPE_ID must not appear in pilot.weapons."""
    from app.analytics.composition import CAPSULE_TYPE_ID, fleet_composition
    from app.config import get_settings
    from app.db.models import FightKill, InventoryType, KillmailAttacker

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)
        km_id = (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
            )
        ).scalar_one()
        # Ensure the capsule InventoryType exists (so FK doesn't fail)
        if not (
            await session.execute(
                select(InventoryType).where(InventoryType.type_id == CAPSULE_TYPE_ID)
            )
        ).scalar_one_or_none():
            session.add(InventoryType(type_id=CAPSULE_TYPE_ID, name="Capsule"))
        # Add an attacker row with weapon_type_id == CAPSULE_TYPE_ID (pod pilot)
        session.add(
            KillmailAttacker(
                killmail_id=km_id,
                attacker_idx=5,
                character_id=ATTACKER,
                ship_type_id=CAPSULE_TYPE_ID,
                weapon_type_id=CAPSULE_TYPE_ID,
                damage_done=0,
                final_blow=False,
            )
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    weapon_type_ids = {w.type_id for w in pilot.weapons}
    assert CAPSULE_TYPE_ID not in weapon_type_ids, (
        f"Capsule type_id {CAPSULE_TYPE_ID} must not appear in pilot.weapons"
    )


@pytest.mark.asyncio
async def test_composition_pilot_weapons_exclude_hull(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """The hull is sometimes logged as a weapon; it must not appear in pilot.weapons."""
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # ATTACKER flies Absolution (the hull)
        km_id = (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
            )
        ).scalar_one()
        # Log the hull itself as the attacker's weapon_type_id.
        await session.execute(
            update(KillmailAttacker)
            .where(KillmailAttacker.killmail_id == km_id, KillmailAttacker.character_id == ATTACKER)
            .values(weapon_type_id=ABSOLUTION)
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert pilot.ship_type_id == ABSOLUTION
    assert ABSOLUTION not in {w.type_id for w in pilot.weapons}, (
        "The hull must not appear in its own pilot.weapons list"
    )
