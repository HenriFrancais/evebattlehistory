"""TDD tests for Task 4.1: damage reconciliation analytics + API endpoints.

Scenario
--------
* Character CHAR_A (2100000001) -- has LogEvents AND is a killmail attacker.
* Character CHAR_B (2200000001) -- has LogEvents but is NOT a killmail attacker
  (the "application truth" case: damage to ships that didn't die).
* fight_1: started_at=2026-06-10 20:00 UTC, ended_at=2026-06-10 20:30 UTC.

Tests:
1.  per-character log_damage_out computed from LogEvents (direction='out', effect_type='damage').
2.  per-character log_damage_in from LogEvents (direction='in', effect_type='damage').
3.  km_damage_attributed from KillmailAttacker rows.
4.  delta = log_out - km_attributed; CHAR_B has delta = log_out (no km attribution).
5.  A character with log_damage_out > 0 but zero km_damage_attributed still appears.
6.  DPS series: bucket timestamps present and sums align to bucket data.
7.  Empty fight (no LogEvents, no killmails) -> empty rows, empty dps_series, no error.
8.  "" effect_type/direction rows are skipped (not included as damage).
9.  API: reconcile endpoint returns expected shape with correct delta.
10. API: 404 when fight_id is not in the BR.
11. API: 404 when br_id does not exist.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    BattleReport,
    BrFight,
    FightKill,
    Killmail,
    KillmailAttacker,
    LogEvent,
    LogEventBucket,
)
from tests.conftest import TEST_TOKEN
from tests.test_association import (
    _SHIP_TYPE_ID,
    CHAR_A,
    CHAR_B,
    FIGHT_END,
    FIGHT_START,
    _ensure_inventory_type,
    _insert_character,
    _insert_fight,
    _insert_gamelog_file,
    _insert_solar_system,
)

CHAR_C = 3300000001

BUCKET_TS_1 = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
BUCKET_TS_2 = dt.datetime(2026, 6, 10, 20, 0, 5, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_br_with_fight(session) -> tuple[str, int, int]:  # type: ignore[no-untyped-def]
    """Insert Fight + BattleReport + BrFight. Returns (br_id, fight_id, km_id)."""
    fight_id = await _insert_fight(
        session,
        victim_char_id=CHAR_A,
        attacker_char_id=CHAR_B,
        started_at=FIGHT_START,
        ended_at=FIGHT_END,
    )
    # Retrieve the km_id that _insert_fight created
    from sqlalchemy import select

    km_id_row = (
        await session.execute(select(FightKill.killmail_id).where(FightKill.fight_id == fight_id))
    ).scalar_one()

    br_id = str(uuid.uuid4())
    session.add(
        BattleReport(
            br_id=br_id,
            source="demo",
            source_url="http://x",
            source_ref="ref",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        )
    )
    session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
    await session.flush()
    return br_id, fight_id, km_id_row


async def _add_log_events(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    character_id: int,
    direction: str,
    effect_type: str,
    amount: float,
    ts: dt.datetime | None = None,
) -> None:
    """Insert a single LogEvent for a fight participant."""
    file_id = await _insert_gamelog_file(session, character_id=character_id)
    ts = ts or (FIGHT_START + dt.timedelta(seconds=10))
    session.add(
        LogEvent(
            file_id=file_id,
            character_id=character_id,
            ts=ts,
            direction=direction,
            effect_type=effect_type,
            amount=amount,
            fight_id=fight_id,
        )
    )
    await session.flush()


async def _add_bucket(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    character_id: int,
    bucket_ts: dt.datetime,
    direction: str = "out",
    effect_type: str = "damage",
    sum_amount: float = 500.0,
    event_count: int = 5,
) -> None:
    session.add(
        LogEventBucket(
            fight_id=fight_id,
            character_id=character_id,
            bucket_ts=bucket_ts,
            effect_type=effect_type,
            direction=direction,
            sum_amount=sum_amount,
            event_count=event_count,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# 1-8: analytics layer
# ---------------------------------------------------------------------------


async def test_reconcile_log_damage_out_per_character(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """log_damage_out is computed from LogEvents direction='out' effect_type='damage'."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _add_log_events(session, fight_id, CHAR_A, "out", "damage", 300.0)
        await _add_log_events(session, fight_id, CHAR_A, "out", "damage", 200.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    char_a_row = next((r for r in result.rows if r.character_id == CHAR_A), None)
    assert char_a_row is not None
    assert char_a_row.log_damage_out == pytest.approx(500.0)


async def test_reconcile_log_damage_in_per_character(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """log_damage_in is computed from LogEvents direction='in' effect_type='damage'."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _add_log_events(session, fight_id, CHAR_A, "in", "damage", 400.0)
        await _add_log_events(session, fight_id, CHAR_A, "in", "damage", 100.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    char_a_row = next((r for r in result.rows if r.character_id == CHAR_A), None)
    assert char_a_row is not None
    assert char_a_row.log_damage_in == pytest.approx(500.0)


async def test_reconcile_km_damage_attributed_from_attacker(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """km_damage_attributed is the sum of KillmailAttacker.damage_done for this fight."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _ensure_inventory_type(session, _SHIP_TYPE_ID)
        km2_id = int(time.monotonic_ns() % 2**30) + 1
        session.add(Killmail(
            killmail_id=km2_id,
            killmail_time=FIGHT_START + dt.timedelta(seconds=60),
            solar_system_id=31002222,
            victim_character_id=CHAR_A,
            victim_ship_type_id=_SHIP_TYPE_ID,
            npc_kill=False,
            solo_kill=False,
        ))
        await session.flush()
        session.add(KillmailAttacker(
            killmail_id=km2_id,
            attacker_idx=0,
            character_id=CHAR_B,
            damage_done=250,
            final_blow=True,
        ))
        session.add(FightKill(fight_id=fight_id, killmail_id=km2_id, side_idx=0))
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    # CHAR_B appears in both km1 (100 from _insert_fight) and km2 (250)
    char_b_row = next((r for r in result.rows if r.character_id == CHAR_B), None)
    assert char_b_row is not None
    assert char_b_row.km_damage_attributed == pytest.approx(350.0)


async def test_reconcile_delta_log_out_minus_km(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """delta = log_damage_out - km_damage_attributed."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        # CHAR_B has log_damage_out=700, km=100 (from _insert_fight)
        await _add_log_events(session, fight_id, CHAR_B, "out", "damage", 700.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    char_b_row = next((r for r in result.rows if r.character_id == CHAR_B), None)
    assert char_b_row is not None
    assert char_b_row.log_damage_out == pytest.approx(700.0)
    assert char_b_row.km_damage_attributed == pytest.approx(100.0)
    assert char_b_row.delta == pytest.approx(600.0)  # 700 - 100


async def test_reconcile_char_with_log_but_no_km_appears(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A character with log_damage_out > 0 but zero km attribution still appears in rows.

    This is the headline 'application truth' case: pilots who shot at ships
    that didn't die are not on any killmail but their damage shows in logs.
    """
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _insert_character(session, CHAR_C)
        # CHAR_C dealt log damage but is not on any killmail
        await _add_log_events(session, fight_id, CHAR_C, "out", "damage", 900.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    char_c_row = next((r for r in result.rows if r.character_id == CHAR_C), None)
    assert char_c_row is not None
    assert char_c_row.log_damage_out == pytest.approx(900.0)
    assert char_c_row.km_damage_attributed == pytest.approx(0.0)
    assert char_c_row.delta == pytest.approx(900.0)


async def test_reconcile_dps_series_aligns_to_buckets(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """dps_series: bucket timestamps and sums come from LogEventBucket (direction=out, damage)."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _add_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "out", "damage", 400.0)
        await _add_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "out", "damage", 200.0)
        await _add_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "out", "damage", 100.0)
        # An "in" direction bucket -- should NOT be in the outgoing DPS series
        await _add_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "in", "damage", 50.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    assert len(result.dps_series) >= 1
    ts1_epoch = int(BUCKET_TS_1.timestamp())
    ts2_epoch = int(BUCKET_TS_2.timestamp())
    x_vals = [point.bucket_ts_epoch for point in result.dps_series]
    assert ts1_epoch in x_vals
    assert ts2_epoch in x_vals
    # Sum at BUCKET_TS_1: 400 + 200 = 600
    ts1_point = next(p for p in result.dps_series if p.bucket_ts_epoch == ts1_epoch)
    assert ts1_point.sum_damage_out == pytest.approx(600.0)


async def test_reconcile_empty_fight_returns_empty(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Empty fight (no LogEvents, no killmails) -> empty rows + empty dps_series, no error."""
    from app.analytics.reconcile import fight_damage_reconcile
    from app.db.models import Fight

    async with db_session_maker() as session:
        await _insert_solar_system(session, 31002222)
        fight = Fight(
            system_id=31002222,
            started_at=FIGHT_START,
            ended_at=FIGHT_END,
            isk_destroyed_total=0.0,
            largest_side_pilots=0,
            capitals_involved=False,
            distinct_alliance_count=0,
        )
        session.add(fight)
        await session.flush()
        empty_fight_id = fight.fight_id
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, empty_fight_id)

    assert result.rows == []
    assert result.dps_series == []


async def test_reconcile_unknown_effect_type_excluded(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """LogEvents with effect_type='' (unknown) are not counted as damage."""
    from app.analytics.reconcile import fight_damage_reconcile

    async with db_session_maker() as session:
        _br_id, fight_id, _km_id = await _make_br_with_fight(session)
        # Only an unknown-effect-type event; no real damage event
        await _add_log_events(session, fight_id, CHAR_A, "out", "", 500.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_damage_reconcile(session, fight_id)

    # CHAR_A has km attribution from _insert_fight but zero log damage out
    char_a_row = next((r for r in result.rows if r.character_id == CHAR_A), None)
    if char_a_row is not None:
        assert char_a_row.log_damage_out == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 9-11: API layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_reconcile_returns_expected_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fights/{fid}/reconcile returns FightReconcile shape."""
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
        br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _add_log_events(session, fight_id, CHAR_B, "out", "damage", 700.0)
        await _add_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "out", "damage", 700.0)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fights/{fight_id}/reconcile", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert "rows" in data
    assert "dps_series" in data
    assert isinstance(data["rows"], list)
    assert len(data["rows"]) >= 1
    row = data["rows"][0]
    assert "character_id" in row
    assert "log_damage_out" in row
    assert "log_damage_in" in row
    assert "km_damage_attributed" in row
    assert "delta" in row
    # CHAR_B: delta should be positive (log > km)
    char_b_data = next((r for r in data["rows"] if r["character_id"] == CHAR_B), None)
    assert char_b_data is not None
    assert char_b_data["delta"] == pytest.approx(600.0)  # 700 - 100

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_reconcile_404_fight_not_in_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET reconcile for a fight that exists but is NOT in the BR -> 404."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import Fight
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
        br_id, _fight_id, _km_id = await _make_br_with_fight(session)
        await _insert_solar_system(session, 31002222)
        other_fight = Fight(
            system_id=31002222,
            started_at=FIGHT_START,
            ended_at=FIGHT_END,
            isk_destroyed_total=0.0,
            largest_side_pilots=0,
            capitals_involved=False,
            distinct_alliance_count=0,
        )
        session.add(other_fight)
        await session.flush()
        other_fight_id = other_fight.fight_id
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fights/{other_fight_id}/reconcile", headers=hdrs)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_reconcile_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET reconcile for unknown BR -> 404."""
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
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get("/api/brs/no-such-br/fights/1/reconcile", headers=hdrs)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_reconcile_character_name_populated(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """character_name is populated in reconcile rows for known characters."""
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
        # _insert_character from test_association inserts CHAR_B with a name
        br_id, fight_id, _km_id = await _make_br_with_fight(session)
        await _add_log_events(session, fight_id, CHAR_B, "out", "damage", 300.0)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fights/{fight_id}/reconcile", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    char_b_row = next((r for r in data["rows"] if r["character_id"] == CHAR_B), None)
    assert char_b_row is not None
    # character_name should be populated (CHAR_B inserted by _insert_fight via _insert_character)
    assert char_b_row["character_name"] is not None
    assert isinstance(char_b_row["character_name"], str)

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
