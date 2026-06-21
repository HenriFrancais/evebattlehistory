"""TDD tests for Task 15: per-loss damage attribution analytics + API endpoint.
TDD tests for Task 16: battle-level damage leaderboard analytics + API endpoint.

Analytics contract (Task 15):
  loss_damage_attribution(session, killmail_id) -> LossDamageAttribution
  - attackers sorted by damage_done desc
  - share = damage_done / total_attributed (0.0 if total is 0)
  - final_blow flag passed through per attacker
  - damage_taken from Killmail.damage_taken

API contract (Task 15):
  GET /api/brs/{br_id}/losses/{killmail_id}/damage
  - 200 with LossDamageAttributionOut shape when killmail is in the BR
  - 404 when killmail is not linked to the given BR (guard via BR↔fight↔killmail join)

Analytics contract (Task 16):
  br_damage_leaderboard(session, br_id) -> BrDamageLeaderboard
  - rows sorted by damage_done desc
  - damage_done sums KillmailAttacker.damage_done per character_id across all kills in the BR
  - share = damage_done / total_attributed (sums to ~1.0)
  - logs_present is False (Task 21 wires the overlay)
  - log_damage_out is None for all rows

API contract (Task 16):
  GET /api/brs/{br_id}/damage-leaderboard
  - 200 with BrDamageLeaderboardOut shape
  - logs_present field is False
  - 404 when BR does not exist
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


# ---------------------------------------------------------------------------
# Task 16: Battle-level damage leaderboard tests
# ---------------------------------------------------------------------------


async def _seed_loss_multi(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    km_id: int,
    char_dmg: list[tuple[int, int]],
) -> int:
    """Seed a Killmail with given (character_id, damage_done) pairs + FightKill link."""
    await _ensure_prereqs(session)
    total = sum(d for _, d in char_dmg)
    session.add(Killmail(
        killmail_id=km_id,
        killmail_time=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
        solar_system_id=_SOLAR_SYSTEM_ID,
        victim_character_id=None,
        victim_ship_type_id=_SHIP_TYPE_ID,
        total_value=1.0,
        damage_taken=total,
        npc_kill=False,
        solo_kill=False,
    ))
    for idx, (char_id, dmg) in enumerate(char_dmg):
        session.add(KillmailAttacker(
            killmail_id=km_id,
            attacker_idx=idx,
            character_id=char_id,
            ship_type_id=_SHIP_TYPE_ID,
            damage_done=dmg,
            final_blow=(idx == 0),
        ))
    session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
    await session.flush()
    return km_id


@pytest.mark.asyncio
async def test_leaderboard_sums_across_kills_sorted_desc(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """br_damage_leaderboard sums damage per character across kills, sorted desc, share ~1."""
    from app.analytics.damage_attribution import br_damage_leaderboard

    # Note: _make_br_with_fight (via _insert_fight) pre-seeds one kill with
    # CHAR_B=2200000001 doing 100 damage.  Our assertions account for that extra row.
    async with db_session_maker() as s:
        await _ensure_character(s, 20, "Pilot_X")
        await _ensure_character(s, 21, "Pilot_Y")
        br_id, fight_id = await _make_br_with_fight(s)
        # kill 1: X=2000, Y=500
        await _seed_loss_multi(s, fight_id, km_id=1001, char_dmg=[(20, 2000), (21, 500)])
        # kill 2: X=1000, Y=1500
        await _seed_loss_multi(s, fight_id, km_id=1002, char_dmg=[(20, 1000), (21, 1500)])
        await s.commit()

    # X total: 3000, Y total: 2000, CHAR_B (pre-seeded): 100, grand total: 5100
    async with db_session_maker() as s:
        lb = await br_damage_leaderboard(s, br_id)

    assert lb.logs_present is False
    assert lb.total_attributed == 5100
    # At least 3 rows (Pilot_X, Pilot_Y, and pre-seeded CHAR_B)
    assert len(lb.rows) >= 2

    # Extract Pilot_X and Pilot_Y rows by character_id
    by_char = {r.character_id: r for r in lb.rows}
    row_x = by_char[20]
    row_y = by_char[21]

    assert row_x.damage_done == 3000
    assert row_x.character_name == "Pilot_X"
    assert row_y.damage_done == 2000
    assert row_y.character_name == "Pilot_Y"

    # sorted desc: Pilot_X (3000) is at the top
    assert lb.rows[0].character_id == 20

    # shares sum to ~1.0
    total_share = sum(r.share for r in lb.rows)
    assert abs(total_share - 1.0) < 1e-6

    # Pilot_X share: 3000/5100
    assert abs(row_x.share - 3000 / 5100) < 1e-6
    # Pilot_Y share: 2000/5100
    assert abs(row_y.share - 2000 / 5100) < 1e-6

    # log_damage_out is None for all rows
    for row in lb.rows:
        assert row.log_damage_out is None


@pytest.mark.asyncio
async def test_leaderboard_multiple_fights_in_br(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """br_damage_leaderboard aggregates across multiple fights within the BR."""
    from app.analytics.damage_attribution import br_damage_leaderboard
    from app.db.models import BrFight, Fight, FightSide

    async with db_session_maker() as s:
        await _ensure_character(s, 30, "Alpha")
        await _ensure_character(s, 31, "Beta")
        # Two fights, both in the same BR
        br_id, fight_id_1 = await _make_br_with_fight(s)

        # Create a second fight and link it
        fight2 = Fight(
            system_id=31002222,
            started_at=dt.datetime(2026, 6, 10, 21, 0, 0, tzinfo=dt.UTC),
            ended_at=dt.datetime(2026, 6, 10, 21, 30, 0, tzinfo=dt.UTC),
            isk_destroyed_total=0.0,
            largest_side_pilots=1,
            capitals_involved=False,
        )
        s.add(fight2)
        await s.flush()
        fight_id_2 = fight2.fight_id
        s.add(FightSide(fight_id=fight_id_2, side_idx=0, side_kind="friendly",
                        pilot_count=1, isk_lost=0.0))
        s.add(BrFight(br_id=br_id, fight_id=fight_id_2, seq=1))
        await s.flush()

        await _ensure_prereqs(s)
        # Fight 1 kill: Alpha=3000
        await _seed_loss_multi(s, fight_id_1, km_id=1010, char_dmg=[(30, 3000)])
        # Fight 2 kill: Alpha=2000, Beta=1000
        await _seed_loss_multi(s, fight_id_2, km_id=1011, char_dmg=[(30, 2000), (31, 1000)])
        await s.commit()

    # Alpha total: 5000, Beta total: 1000, CHAR_B (pre-seeded by _make_br_with_fight): 100
    # grand_total: 6100
    async with db_session_maker() as s:
        lb = await br_damage_leaderboard(s, br_id)

    assert lb.total_attributed == 6100
    by_char = {r.character_id: r for r in lb.rows}
    assert by_char[30].damage_done == 5000
    assert by_char[31].damage_done == 1000
    # Alpha is still first (5000 > 1000)
    assert lb.rows[0].character_id == 30
    total_share = sum(r.share for r in lb.rows)
    assert abs(total_share - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Task 16: API contract tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_endpoint_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/damage-leaderboard returns correct shape with logs_present=False."""
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
        await _ensure_character(s, 40, "Warrior_A")
        await _ensure_character(s, 41, "Warrior_B")
        br_id, fight_id = await _make_br_with_fight(s)
        # _make_br_with_fight pre-seeds CHAR_B (2200000001) with 100 damage
        await _seed_loss_multi(s, fight_id, km_id=2001, char_dmg=[(40, 4000), (41, 1000)])
        await s.commit()

    # Warrior_A: 4000, Warrior_B: 1000, CHAR_B (pre-seeded): 100 → total 5100
    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        r = client.get(f"/api/brs/{br_id}/damage-leaderboard", headers=MEMBER_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["logs_present"] is False
        assert body["total_attributed"] == 5100
        rows = body["rows"]
        assert len(rows) >= 2
        # sorted desc: Warrior_A (4000) is first
        assert rows[0]["damage_done"] == 4000
        assert rows[0]["log_damage_out"] is None
        # Find Warrior_A and Warrior_B by character_id
        by_char = {row["character_id"]: row for row in rows}
        assert by_char[40]["character_name"] == "Warrior_A"
        assert by_char[41]["character_name"] == "Warrior_B"
        assert abs(by_char[40]["share"] - 4000 / 5100) < 1e-6
        assert abs(by_char[41]["share"] - 1000 / 5100) < 1e-6


# ---------------------------------------------------------------------------
# Task 21: Augmentation seam — log overlay tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaderboard_seam_no_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """BR with NO log events → every row log_damage_out is None, logs_present is False."""
    from app.analytics.damage_attribution import br_damage_leaderboard

    async with db_session_maker() as s:
        await _ensure_character(s, 50, "Pilot_NoLog")
        br_id, fight_id = await _make_br_with_fight(s)
        # Seed killmail damage only — no LogEvent rows
        await _seed_loss_multi(s, fight_id, km_id=3001, char_dmg=[(50, 2500)])
        await s.commit()

    async with db_session_maker() as s:
        lb = await br_damage_leaderboard(s, br_id)

    assert lb.logs_present is False
    for row in lb.rows:
        assert row.log_damage_out is None, (
            f"expected None for char {row.character_id}, got {row.log_damage_out}"
        )


@pytest.mark.asyncio
async def test_leaderboard_seam_with_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """BR WITH log rows → matching character gets non-null log_damage_out, logs_present True."""
    import datetime as _dt

    from app.analytics.damage_attribution import br_damage_leaderboard
    from app.db.models import GamelogFile, LogEvent

    CHAR_WITH_LOG = 60
    CHAR_NO_LOG = 61

    async with db_session_maker() as s:
        await _ensure_character(s, CHAR_WITH_LOG, "Pilot_WithLog")
        await _ensure_character(s, CHAR_NO_LOG, "Pilot_NoLog2")
        br_id, fight_id = await _make_br_with_fight(s)
        # Killmail damage for both characters
        await _seed_loss_multi(
            s, fight_id, km_id=3002,
            char_dmg=[(CHAR_WITH_LOG, 2000), (CHAR_NO_LOG, 500)],
        )
        # Seed a GamelogFile + LogEvent (damage:out) for CHAR_WITH_LOG only
        gf = GamelogFile(
            uploaded_by_user="u",
            claimed_character_id=CHAR_WITH_LOG,
            resolved_via="filename",
            stored_path="/test/log.txt",
            sha256="sha256seam60",
            mime="text/plain",
            size=1,
            parse_status="parsed",
            event_count=1,
            uploaded_at=_dt.datetime.now(_dt.UTC),
        )
        s.add(gf)
        await s.flush()
        s.add(LogEvent(
            file_id=gf.file_id,
            character_id=CHAR_WITH_LOG,
            ts=_dt.datetime(2026, 6, 10, 20, 5, 0, tzinfo=_dt.UTC),
            effect_type="damage",
            direction="out",
            amount=3500.0,
            fight_id=fight_id,
        ))
        await s.commit()

    async with db_session_maker() as s:
        lb = await br_damage_leaderboard(s, br_id)

    assert lb.logs_present is True
    by_char = {r.character_id: r for r in lb.rows}
    # Character with logs: log_damage_out should be set (3500.0)
    assert by_char[CHAR_WITH_LOG].log_damage_out is not None
    assert by_char[CHAR_WITH_LOG].log_damage_out == pytest.approx(3500.0)
    # Character without logs: log_damage_out stays None
    assert by_char[CHAR_NO_LOG].log_damage_out is None
    # Killmail ordering is preserved: CHAR_WITH_LOG (2000) first
    assert lb.rows[0].character_id == CHAR_WITH_LOG


@pytest.mark.asyncio
async def test_leaderboard_endpoint_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/damage-leaderboard → 404 when BR does not exist."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        r = client.get(
            f"/api/brs/{uuid.uuid4()}/damage-leaderboard",
            headers=MEMBER_HEADERS,
        )
        assert r.status_code == 404
