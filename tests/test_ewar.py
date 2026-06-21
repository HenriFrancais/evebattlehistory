"""TDD tests for Task 4.1: EWAR/logi effectiveness analytics + API endpoints.

Scenario
--------
* Character CHAR_A (2100000001) -- applies scram outgoing, receives jam incoming.
* Character CHAR_B (2200000001) -- applies reps outgoing (logi).
* Character CHAR_C (3300000001) -- applies neut outgoing (cap warfare).
* fight_1: started_at=2026-06-10 20:00 UTC, ended_at=2026-06-10 20:30 UTC.

Tests:
1.  Tackle/EWAR: scram out event appears in ewar list with effect_type='scram', direction='out'.
2.  Tackle/EWAR: jam in event appears with effect_type='jam', direction='in'.
3.  Cap warfare: neut out totals summed per character + direction.
4.  Logi: rep_armor out totals summed, direction='out'.
5.  Logi: multiple rep types (rep_armor + rep_shield) summed separately.
6.  first_ts / last_ts set correctly from raw events.
7.  Empty fight -> empty ewar/cap/logi lists, no error.
8.  "" effect_type/direction events are NOT included in any EWAR category.
9.  API: ewar endpoint returns expected shape.
10. API: fight not in BR -> 404.
11. API: unknown BR -> 404.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    BattleReport,
    BrFight,
    LogEvent,
)
from tests.conftest import TEST_TOKEN
from tests.test_association import (
    CHAR_A,
    CHAR_B,
    FIGHT_END,
    FIGHT_START,
    _insert_character,
    _insert_fight,
    _insert_gamelog_file,
    _insert_solar_system,
)

CHAR_C = 3300000001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_br_with_fight(session) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    """Insert Fight + BattleReport + BrFight. Returns (br_id, fight_id)."""
    fight_id = await _insert_fight(
        session,
        victim_char_id=CHAR_A,
        attacker_char_id=CHAR_B,
        started_at=FIGHT_START,
        ended_at=FIGHT_END,
    )
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
    return br_id, fight_id


async def _add_event(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    character_id: int,
    direction: str,
    effect_type: str,
    amount: float = 0.0,
    ts: dt.datetime | None = None,
    other_name: str | None = None,
    source_name: str | None = None,
    target_name: str | None = None,
    authoritative: bool = False,
    dedupe_suppressed: bool = False,
) -> None:
    file_id = await _insert_gamelog_file(session, character_id=character_id)
    ts = ts or (FIGHT_START + dt.timedelta(seconds=10))
    session.add(
        LogEvent(
            file_id=file_id,
            character_id=character_id,
            ts=ts,
            direction=direction,
            effect_type=effect_type,
            amount=amount if amount else None,
            fight_id=fight_id,
            other_name=other_name,
            source_name=source_name,
            target_name=target_name,
            authoritative=authoritative,
            dedupe_suppressed=dedupe_suppressed,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# 1-8: analytics layer
# ---------------------------------------------------------------------------


async def test_ewar_scram_out_appears_in_ewar_list(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A scram direction='out' event surfaces in FightEwar.ewar with correct fields."""
    from app.analytics.ewar import fight_ewar

    ts1 = FIGHT_START + dt.timedelta(seconds=5)
    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        # scram/disrupt rows are now keyed by source_name/target_name, not character_id
        await _add_event(session, fight_id, CHAR_A, "out", "scram", ts=ts1, other_name="Victim",
                         source_name="Attacker Alpha", target_name="Victim Bravo")
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    scram_rows = [e for e in result.ewar if e.effect_type == "scram" and e.direction == "out"]
    assert len(scram_rows) == 1
    row = scram_rows[0]
    assert row.source_name == "Attacker Alpha"
    assert row.target_name == "Victim Bravo"
    assert row.event_count >= 1
    assert row.first_ts is not None
    assert row.last_ts is not None


async def test_ewar_jam_in_appears_in_ewar_list(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A jam direction='in' event surfaces in FightEwar.ewar with direction='in'."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_A, "in", "jam")
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    jam_rows = [e for e in result.ewar if e.effect_type == "jam" and e.direction == "in"]
    assert len(jam_rows) == 1
    assert jam_rows[0].character_id == CHAR_A


async def test_ewar_cap_warfare_neut_totals(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Neut out events sum to correct total in FightEwar.cap."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _insert_character(session, CHAR_C)
        await _add_event(session, fight_id, CHAR_C, "out", "neut", amount=300.0)
        await _add_event(session, fight_id, CHAR_C, "out", "neut", amount=200.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    cap_rows = [c for c in result.cap if c.character_id == CHAR_C and c.direction == "out"]
    assert len(cap_rows) == 1
    assert cap_rows[0].sum_amount == pytest.approx(500.0)
    assert cap_rows[0].effect_type == "neut"


async def test_ewar_logi_rep_armor_out_totals(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """rep_armor direction='out' events sum correctly in FightEwar.logi."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_B, "out", "rep_armor", amount=1000.0)
        await _add_event(session, fight_id, CHAR_B, "out", "rep_armor", amount=500.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    logi_rows = [
        logi for logi in result.logi
        if logi.character_id == CHAR_B
        and logi.effect_type == "rep_armor"
        and logi.direction == "out"
    ]
    assert len(logi_rows) == 1
    assert logi_rows[0].sum_amount == pytest.approx(1500.0)


async def test_ewar_logi_multiple_rep_types_separate(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """rep_armor and rep_shield are tracked separately in FightEwar.logi."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_B, "out", "rep_armor", amount=800.0)
        await _add_event(session, fight_id, CHAR_B, "out", "rep_shield", amount=400.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    armor_rows = [logi for logi in result.logi if logi.effect_type == "rep_armor"]
    shield_rows = [logi for logi in result.logi if logi.effect_type == "rep_shield"]
    assert len(armor_rows) >= 1
    assert len(shield_rows) >= 1
    assert armor_rows[0].sum_amount == pytest.approx(800.0)
    assert shield_rows[0].sum_amount == pytest.approx(400.0)


async def test_ewar_first_and_last_ts(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """first_ts / last_ts are set from the earliest/latest event timestamps."""
    from app.analytics.ewar import fight_ewar

    ts1 = FIGHT_START + dt.timedelta(seconds=10)
    ts2 = FIGHT_START + dt.timedelta(seconds=60)
    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        # disrupt is now keyed by source_name/target_name; two events for same pair aggregate
        await _add_event(session, fight_id, CHAR_A, "out", "disrupt", ts=ts1,
                         source_name="Disruptor One", target_name="Target Two")
        await _add_event(session, fight_id, CHAR_A, "out", "disrupt", ts=ts2,
                         source_name="Disruptor One", target_name="Target Two")
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    disrupt_row = next(
        (e for e in result.ewar if e.effect_type == "disrupt" and e.source_name == "Disruptor One"),
        None,
    )
    assert disrupt_row is not None
    assert disrupt_row.event_count == 2
    assert disrupt_row.first_ts <= disrupt_row.last_ts


async def test_ewar_empty_fight_returns_empty(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Empty fight -> all EWAR/cap/logi lists are empty, no error."""
    from app.analytics.ewar import fight_ewar
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
        result = await fight_ewar(session, empty_fight_id)

    assert result.ewar == []
    assert result.cap == []
    assert result.logi == []


async def test_ewar_unknown_effect_type_excluded(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Events with effect_type='' (unknown) are excluded from EWAR/cap/logi summaries."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_A, "out", "", amount=100.0)
        await session.commit()

    async with db_session_maker() as session:
        result = await fight_ewar(session, fight_id)

    assert all(e.effect_type != "" for e in result.ewar)
    assert all(c.effect_type != "" for c in result.cap)
    assert all(logi.effect_type != "" for logi in result.logi)


# ---------------------------------------------------------------------------
# 9-11: API layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_ewar_returns_expected_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fights/{fid}/ewar returns FightEwar shape."""
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
        await _add_event(session, fight_id, CHAR_A, "out", "scram")
        await _add_event(session, fight_id, CHAR_B, "out", "rep_armor", amount=500.0)
        await _add_event(session, fight_id, CHAR_A, "out", "neut", amount=200.0)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fights/{fight_id}/ewar", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert "ewar" in data
    assert "cap" in data
    assert "logi" in data
    assert isinstance(data["ewar"], list)
    assert isinstance(data["cap"], list)
    assert isinstance(data["logi"], list)
    scram_entries = [e for e in data["ewar"] if e["effect_type"] == "scram"]
    assert len(scram_entries) >= 1
    logi_entries = [logi for logi in data["logi"] if logi["effect_type"] == "rep_armor"]
    assert len(logi_entries) >= 1

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_ewar_404_fight_not_in_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET ewar for a fight that exists but is NOT in the BR -> 404."""
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
        br_id, _fight_id = await _make_br_with_fight(session)
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
        resp = client.get(f"/api/brs/{br_id}/fights/{other_fight_id}/ewar", headers=hdrs)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_ewar_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET ewar for unknown BR -> 404."""
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
        resp = client.get("/api/brs/no-such-br/fights/1/ewar", headers=hdrs)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 12: friendly-on-friendly attribution (Task 5)
# ---------------------------------------------------------------------------


async def test_ewar_no_friendly_on_friendly_attribution(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A suppressed third-party friendly-on-friendly observation must not appear; the
    authoritative row names the real source/target."""
    from app.analytics.ewar import fight_ewar

    async with db_session_maker() as session:
        _br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_C, "in", "scram",
                         source_name="AllyChar Kyte", target_name="AllyChar Boop",
                         authoritative=False, dedupe_suppressed=True)
        await _add_event(session, fight_id, CHAR_A, "out", "scram",
                         source_name="AllyChar Kyte", target_name="FakeEnemy Delta",
                         authoritative=True, dedupe_suppressed=False)
        await session.commit()
        result = await fight_ewar(session, fight_id)
    sources = {r.source_name for r in result.ewar}
    targets = {r.target_name for r in result.ewar}
    assert "AllyChar Boop" not in targets       # suppressed friendly-on-friendly gone
    assert "AllyChar Kyte" in sources            # real tackler counted once
    assert "FakeEnemy Delta" in targets
