"""TDD tests for E3: fleet-level timeline analytics + API endpoint.

Scenario
--------
* The fleet timeline aggregates LogEventBucket rows across ALL characters
  for all fights in a BR, producing four fixed series:
    - dps_out     : effect_type='damage', direction='out', sum_amount-based
    - remote_rep  : effect_type in ('rep_armor','rep_shield'), direction='out', sum_amount-based
    - ewar        : effect_type in ('scram','disrupt','jam'), event_count-based (NOT sum_amount)
    - cap_warfare : effect_type in ('neut','nos','cap_transfer'), sum_amount-based
* Kills come from FightKill + Killmail + FightSide + InventoryType.
* No per-character access gate — visible to all authenticated users.

Tests:
1.  dps_out sums across 2 characters at the same bucket_ts.
2.  Alignment: every series values length == len(x), even with None gaps.
3.  remote_rep pulls rep_armor + rep_shield.
4.  ewar is count-based (event_count, not sum_amount).
5.  cap_warfare pulls neut, nos, cap_transfer (sum_amount-based).
6.  kills list has correct entries (ts, killmail_id, victim_character_id,
    victim_ship_name, side_kind, isk) sorted by ts.
7.  Empty BR (no logs / no kills) → all empty arrays, no error.
8.  API 200 returns correct shape (x, series, kills, bucket_seconds, fights,
    t_start, t_end).
9.  API 404 for unknown BR.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    BattleReport,
    BrFight,
    FightKill,
    FightSide,
    Killmail,
    LogEventBucket,
)
from tests.test_association import _insert_fight

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAR_A = 2100000001
CHAR_B = 2200000001

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC)

BUCKET_TS_1 = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
BUCKET_TS_2 = dt.datetime(2026, 6, 10, 20, 0, 5, tzinfo=dt.UTC)

_SHIP_TYPE_ID = 1  # inserted by _insert_fight via _ensure_inventory_type


# ---------------------------------------------------------------------------
# Local test helpers (mirror pattern from test_timeline.py)
# ---------------------------------------------------------------------------


async def _make_br_with_fight(session):  # type: ignore[no-untyped-def]
    """Insert a Fight + BattleReport + BrFight + FightSide rows.

    Returns (br_id, fight_id).
    FightSide: side_idx=0 → friendly, side_idx=1 → hostile.
    """
    fight_id = await _insert_fight(
        session,
        victim_char_id=CHAR_A,
        attacker_char_id=CHAR_B,
        started_at=FIGHT_START,
        ended_at=FIGHT_END,
    )
    # Add FightSide rows so kills can resolve side_kind
    session.add(FightSide(fight_id=fight_id, side_idx=0, side_kind="friendly",
                          pilot_count=1, isk_lost=0.0))
    session.add(FightSide(fight_id=fight_id, side_idx=1, side_kind="hostile",
                          pilot_count=1, isk_lost=0.0))
    await session.flush()

    br_id = str(uuid.uuid4())
    session.add(BattleReport(
        br_id=br_id,
        source="demo",
        source_url="http://x",
        source_ref="ref",
        created_by_user="test",
        status="ready",
        progress_pct=100,
        created_at=dt.datetime.now(dt.UTC),
    ))
    session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
    await session.flush()
    return br_id, fight_id


async def _insert_bucket(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    character_id: int,
    bucket_ts: dt.datetime,
    effect_type: str = "damage",
    direction: str = "out",
    sum_amount: float = 500.0,
    event_count: int = 5,
) -> None:
    """Insert a single LogEventBucket row."""
    session.add(LogEventBucket(
        fight_id=fight_id,
        character_id=character_id,
        bucket_ts=bucket_ts,
        effect_type=effect_type,
        direction=direction,
        sum_amount=sum_amount,
        event_count=event_count,
    ))
    await session.flush()


async def _insert_killmail(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    side_idx: int,
    victim_char_id: int,
    ship_type_id: int,
    total_value: float,
    killmail_time: dt.datetime,
) -> int:
    """Insert a Killmail + FightKill and return killmail_id."""
    km_id = abs(hash((fight_id, side_idx, victim_char_id, str(killmail_time)))) % (2**30)
    session.add(Killmail(
        killmail_id=km_id,
        killmail_time=killmail_time,
        solar_system_id=31002222,
        victim_character_id=victim_char_id,
        victim_ship_type_id=ship_type_id,
        total_value=total_value,
        npc_kill=False,
        solo_kill=False,
    ))
    await session.flush()
    session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=side_idx))
    await session.flush()
    return km_id


# ---------------------------------------------------------------------------
# 1. dps_out sums across 2 characters at the same bucket_ts
# ---------------------------------------------------------------------------


async def test_dps_out_sums_across_characters(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Fleet dps_out = sum of damage:out across all characters at same bucket_ts."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # CHAR_A: damage out 300
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 300.0, 3)
        # CHAR_B: damage out 200 — same bucket_ts
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "out", 200.0, 2)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    expected_x = int(BUCKET_TS_1.timestamp())
    assert expected_x in tl.x
    idx = tl.x.index(expected_x)

    dps_out = next(s for s in tl.series if s.key == "dps_out")
    assert dps_out.values[idx] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# 2. Alignment: every series values length == len(x), even with None gaps
# ---------------------------------------------------------------------------


async def test_alignment_all_series_same_length_as_x(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """All four series have len(values) == len(x), with None gaps where no data."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # dps_out only at BUCKET_TS_1; ewar only at BUCKET_TS_2
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "scram", "out", 0.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    # x must have both timestamps
    assert len(tl.x) == 2
    # All 4 series must have same length as x
    assert len(tl.series) == 4
    for s in tl.series:
        assert len(s.values) == len(tl.x), f"series {s.key} length mismatch"

    # dps_out has None at BUCKET_TS_2 position, ewar has None at BUCKET_TS_1 position
    ts1_idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ts2_idx = tl.x.index(int(BUCKET_TS_2.timestamp()))

    dps_out = next(s for s in tl.series if s.key == "dps_out")
    assert dps_out.values[ts1_idx] is not None
    assert dps_out.values[ts2_idx] is None

    ewar = next(s for s in tl.series if s.key == "ewar")
    assert ewar.values[ts1_idx] is None
    assert ewar.values[ts2_idx] is not None


# ---------------------------------------------------------------------------
# 3. remote_rep pulls rep_armor + rep_shield
# ---------------------------------------------------------------------------


async def test_remote_rep_aggregates_rep_armor_and_rep_shield(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """remote_rep combines rep_armor and rep_shield at the same bucket_ts."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep_armor", "out", 400.0, 4)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "rep_shield", "out", 150.0, 3)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    remote_rep = next(s for s in tl.series if s.key == "remote_rep")
    assert remote_rep.values[idx] == pytest.approx(550.0)


# ---------------------------------------------------------------------------
# 4. ewar is count-based (event_count, not sum_amount)
# ---------------------------------------------------------------------------


async def test_ewar_uses_event_count_not_sum_amount(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """ewar series uses event_count, ignoring sum_amount."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # scram: event_count=7, sum_amount=9999 (should be ignored)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "scram", "out",
                             sum_amount=9999.0, event_count=7)
        # disrupt: event_count=3
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "disrupt", "out",
                             sum_amount=1234.0, event_count=3)
        # jam: event_count=2 at BUCKET_TS_2
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_2, "jam", "out",
                             sum_amount=0.0, event_count=2)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    ts1_idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ts2_idx = tl.x.index(int(BUCKET_TS_2.timestamp()))

    ewar = next(s for s in tl.series if s.key == "ewar")
    # BUCKET_TS_1: scram(7) + disrupt(3) = 10 (count-based)
    assert ewar.values[ts1_idx] == pytest.approx(10.0)
    # BUCKET_TS_2: jam(2)
    assert ewar.values[ts2_idx] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 5. cap_warfare pulls neut, nos, cap_transfer (sum_amount-based)
# ---------------------------------------------------------------------------


async def test_cap_warfare_aggregates_neut_nos_cap_transfer(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """cap_warfare combines neut, nos, and cap_transfer using sum_amount."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "neut", "out", 200.0, 2)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "nos", "out", 50.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "cap_transfer", "out", 80.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    cap_warfare = next(s for s in tl.series if s.key == "cap_warfare")
    assert cap_warfare.values[idx] == pytest.approx(330.0)


# ---------------------------------------------------------------------------
# 6. kills list has correct entries
# ---------------------------------------------------------------------------


async def test_kills_list_correct_entries(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """kills list has ts, killmail_id, victim_character_id, victim_ship_name, side_kind, isk."""
    from app.analytics.fleet import fleet_timeline

    km_time = FIGHT_START + dt.timedelta(seconds=60)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # _insert_fight already created a killmail on side_idx=0 (from _insert_fight impl)
        # We insert an additional killmail on side_idx=1 (hostile)
        # Make sure ship type exists first (already done by _insert_fight for type_id=1)
        km_id = await _insert_killmail(
            session,
            fight_id=fight_id,
            side_idx=1,
            victim_char_id=CHAR_B,
            ship_type_id=_SHIP_TYPE_ID,
            total_value=500_000_000.0,
            killmail_time=km_time,
        )
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    # Find the kill we inserted explicitly (side_idx=1 → hostile)
    our_kill = next((k for k in tl.kills if k.killmail_id == km_id), None)
    assert our_kill is not None
    assert our_kill.victim_character_id == CHAR_B
    assert our_kill.victim_ship_name == "TestShip"
    assert our_kill.side_kind == "hostile"
    assert our_kill.isk == pytest.approx(500_000_000.0)
    assert our_kill.ts == int(km_time.timestamp())

    # kills must be sorted by ts ascending
    assert tl.kills == sorted(tl.kills, key=lambda k: k.ts)


# ---------------------------------------------------------------------------
# 7. Empty BR → empty series + empty kills, no error
# ---------------------------------------------------------------------------


async def test_empty_br_no_logs_no_kills(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """BR with no logs and no kills returns empty x, 4 empty series, empty kills."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id = str(uuid.uuid4())
        session.add(BattleReport(
            br_id=br_id,
            source="demo",
            source_url="http://x",
            source_ref="ref",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        ))
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    assert tl.x == []
    assert len(tl.series) == 4
    for s in tl.series:
        assert s.values == []
    assert tl.kills == []
    assert tl.t_start is None
    assert tl.t_end is None


# ---------------------------------------------------------------------------
# 8. API 200 returns correct shape
# ---------------------------------------------------------------------------


from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN  # noqa: E402


@pytest.mark.asyncio
async def test_api_fleet_timeline_returns_correct_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fleet-timeline returns correct shape with 200."""
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
    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 250.0, 5)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=CREATOR_HEADERS)

    assert resp.status_code == 200
    data = resp.json()

    assert "x" in data
    assert "series" in data
    assert "kills" in data
    assert "bucket_seconds" in data
    assert "fights" in data
    assert "t_start" in data
    assert "t_end" in data

    assert isinstance(data["x"], list)
    assert isinstance(data["series"], list)
    assert isinstance(data["kills"], list)
    assert isinstance(data["fights"], list)
    assert data["bucket_seconds"] == 5

    # Exactly 4 series
    assert len(data["series"]) == 4
    series_keys = {s["key"] for s in data["series"]}
    assert series_keys == {"dps_out", "remote_rep", "ewar", "cap_warfare"}

    # All values arrays aligned to x
    for s in data["series"]:
        assert len(s["values"]) == len(data["x"]), f"series {s['key']} alignment broken"

    # dps_out should have data at BUCKET_TS_1
    dps_out = next(s for s in data["series"] if s["key"] == "dps_out")
    assert any(v is not None and v > 0 for v in dps_out["values"])

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 9. API 404 for unknown BR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_fleet_timeline_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fleet-timeline returns 404 for unknown BR."""
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
        resp = client.get("/api/brs/no-such-br/fleet-timeline", headers=CREATOR_HEADERS)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# I2. Non-elevated member also gets 200 (no per-character gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_fleet_timeline_accessible_by_member(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fleet-timeline returns 200 for a non-elevated member user."""
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
    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 2)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=MEMBER_HEADERS)

    assert resp.status_code == 200, (
        f"Expected 200 for member user, got {resp.status_code}: {resp.text}"
    )

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# M4. Fights + buckets but zero kills → fleet series populated, kills == []
# ---------------------------------------------------------------------------


async def _make_br_with_fight_no_kills(session):  # type: ignore[no-untyped-def]
    """Insert a Fight + BattleReport + BrFight rows with NO killmails.

    Returns (br_id, fight_id).
    """
    from sqlalchemy import select

    from app.db.models import BattleReport, BrFight, Character, Fight, FightSide, SolarSystem

    system_id = 31002223  # distinct system to avoid collision

    # Ensure solar system exists
    res = await session.execute(select(SolarSystem).where(SolarSystem.system_id == system_id))
    if res.scalar_one_or_none() is None:
        session.add(SolarSystem(system_id=system_id, name="J-TestNoKills", security=None))
        await session.flush()

    # Ensure characters exist
    for cid in (CHAR_A, CHAR_B):
        res = await session.execute(select(Character).where(Character.character_id == cid))
        if res.scalar_one_or_none() is None:
            import datetime as _dt
            session.add(Character(character_id=cid, name=f"Char{cid}",
                                  last_seen_at=_dt.datetime.now(_dt.UTC)))
            await session.flush()

    fight = Fight(
        system_id=system_id,
        started_at=FIGHT_START,
        ended_at=FIGHT_END,
        isk_destroyed_total=0.0,
        largest_side_pilots=2,
        capitals_involved=False,
        distinct_alliance_count=1,
    )
    session.add(fight)
    await session.flush()

    session.add(FightSide(fight_id=fight.fight_id, side_idx=0, side_kind="friendly",
                          pilot_count=1, isk_lost=0.0))
    session.add(FightSide(fight_id=fight.fight_id, side_idx=1, side_kind="hostile",
                          pilot_count=1, isk_lost=0.0))
    await session.flush()

    br_id = str(uuid.uuid4())
    session.add(BattleReport(
        br_id=br_id,
        source="demo",
        source_url="http://x",
        source_ref="ref",
        created_by_user="test",
        status="ready",
        progress_pct=100,
        created_at=dt.datetime.now(dt.UTC),
    ))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.flush()
    return br_id, fight.fight_id


async def test_fights_with_buckets_but_zero_kills(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """BR with fight buckets but no killmails: fleet series populated, kills empty."""
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight_no_kills(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 300.0, 3)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "rep_armor", "out", 100.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    # Fleet series should be populated
    assert len(tl.x) > 0
    dps_out = next(s for s in tl.series if s.key == "dps_out")
    assert any(v is not None and v > 0 for v in dps_out.values)

    # kills must be empty — no killmails were inserted
    assert tl.kills == []
