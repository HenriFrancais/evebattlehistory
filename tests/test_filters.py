"""TDD tests for Task 4.2: capitals detection, filter engine, filter API endpoints."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import MEMBER_HEADERS


# Test 1: is_capital_type_name
def test_is_capital_type_name_lif_is_capital():
    from app.fights.capitals import is_capital_type_name
    assert is_capital_type_name("Lif") is True

def test_is_capital_type_name_case_insensitive():
    from app.fights.capitals import is_capital_type_name
    assert is_capital_type_name("lif") is True
    assert is_capital_type_name("LIF") is True

def test_is_capital_type_name_rifter_is_not():
    from app.fights.capitals import is_capital_type_name
    assert is_capital_type_name("Rifter") is False

def test_is_capital_type_name_all_capitals_known():
    from app.fights.capitals import is_capital_type_name
    for name in ["Nidhoggur", "Thanatos", "Archon", "Chimera",
                 "Hel", "Wyvern", "Aeon", "Nyx",
                 "Naglfar", "Moros", "Revelation", "Phoenix",
                 "Lif", "Minokawa", "Ninazu", "Apostle",
                 "Ragnarok", "Erebus", "Avatar", "Leviathan",
                 "Rorqual", "Bowhead"]:
        assert is_capital_type_name(name), f"{name} should be a capital"

# Test 2: backfill_capitals
@pytest.mark.asyncio
async def test_backfill_capitals_flips_fight_with_capital(db_session_maker):
    from sqlalchemy import select

    from app.db.models import Fight, FightShipCount, InventoryType, SolarSystem
    from app.fights.capitals import backfill_capitals

    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31000001, name="J-Test", security=None))
        await session.flush()

        # Lif (Force Auxiliary = capital)
        session.add(InventoryType(
            type_id=999001, name="Lif", group_id=0,
            group_name="Unknown", category_id=0, category_name="Unknown",
        ))
        await session.flush()

        fight = Fight(
            system_id=31000001,
            started_at=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
            ended_at=dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC),
            isk_destroyed_total=1e9,
            largest_side_pilots=5,
            capitals_involved=False,
            distinct_alliance_count=2,
        )
        session.add(fight)
        await session.flush()

        session.add(FightShipCount(
            fight_id=fight.fight_id, side_idx=0, ship_type_id=999001, count=1,
        ))
        await session.commit()

    async with db_session_maker() as session:
        count = await backfill_capitals(session)
        await session.commit()

    async with db_session_maker() as session:
        updated_fight = (await session.execute(select(Fight))).scalar_one()
        assert updated_fight.capitals_involved is True

    assert count == 1

# Test 3: compile_fight_filter - simple leaf
def test_compile_fight_filter_isk_gte():
    from app.analytics.filters import compile_fight_filter
    stmt = compile_fight_filter({"field": "isk_destroyed_total", "op": ">=", "value": 1e9})
    # Should be a Select statement targeting Fight
    assert stmt is not None
    compiled_str = str(stmt.compile())
    assert "fight" in compiled_str.lower()
    assert "isk_destroyed_total" in compiled_str

# Test 4: compile_fight_filter - AND group
def test_compile_fight_filter_and_group():
    from app.analytics.filters import compile_fight_filter
    stmt = compile_fight_filter({
        "op": "and",
        "clauses": [
            {"field": "isk_destroyed_total", "op": ">=", "value": 1e9},
            {"field": "largest_side_pilots", "op": ">=", "value": 50},
        ]
    })
    assert stmt is not None

# Test 5: ship_count leaf matching (integration with DB)
@pytest.mark.asyncio
async def test_ship_count_filter_matches_fight_with_enough_bhaalgorns(db_session_maker):

    from app.analytics.filters import compile_fight_filter
    from app.db.models import Fight, FightShipCount, FightSide, InventoryType, SolarSystem

    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31000002, name="J-Test2", security=None))
        await session.flush()
        session.add(InventoryType(
            type_id=17920, name="Bhaalgorn", group_id=0,
            group_name="Unknown", category_id=0, category_name="Unknown",
        ))
        await session.flush()

        # Fight with 6 Bhaalgorns on friendly side → should match
        fight_match = Fight(
            system_id=31000002,
            started_at=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
            ended_at=dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC),
            isk_destroyed_total=1e9,
            largest_side_pilots=10,
            capitals_involved=False,
            distinct_alliance_count=2,
        )
        session.add(fight_match)
        await session.flush()
        session.add(FightSide(
            fight_id=fight_match.fight_id, side_idx=0,
            pilot_count=10, isk_lost=1e9, side_kind="friendly",
        ))
        session.add(FightShipCount(
            fight_id=fight_match.fight_id, side_idx=0, ship_type_id=17920, count=6,
        ))

        # Fight with only 5 Bhaalgorns → should NOT match
        fight_no_match = Fight(
            system_id=31000002,
            started_at=dt.datetime(2026, 6, 10, 21, 0, 0, tzinfo=dt.UTC),
            ended_at=dt.datetime(2026, 6, 10, 21, 30, 0, tzinfo=dt.UTC),
            isk_destroyed_total=5e8,
            largest_side_pilots=5,
            capitals_involved=False,
            distinct_alliance_count=2,
        )
        session.add(fight_no_match)
        await session.flush()
        session.add(FightSide(
            fight_id=fight_no_match.fight_id, side_idx=0,
            pilot_count=5, isk_lost=5e8, side_kind="friendly",
        ))
        session.add(FightShipCount(
            fight_id=fight_no_match.fight_id, side_idx=0, ship_type_id=17920, count=5,
        ))
        await session.commit()

    async with db_session_maker() as session:
        stmt = compile_fight_filter({
            "field": "ship_count",
            "ship": "Bhaalgorn",
            "op": ">=",
            "count": 6,
            "side": "friendly",
        })
        result = await session.execute(stmt)
        fights = list(result.scalars())
        fight_ids = [f.fight_id for f in fights]
        assert fight_match.fight_id in fight_ids
        assert fight_no_match.fight_id not in fight_ids

# Test 6: unknown field → FilterError
def test_compile_fight_filter_unknown_field_raises():
    from app.analytics.filters import FilterError, compile_fight_filter
    with pytest.raises(FilterError):
        compile_fight_filter({"field": "nonexistent_field", "op": ">=", "value": 0})

# Test 7: unknown op → FilterError
def test_compile_fight_filter_unknown_op_raises():
    from app.analytics.filters import FilterError, compile_fight_filter
    with pytest.raises(FilterError):
        compile_fight_filter({"field": "isk_destroyed_total", "op": "LIKE", "value": "%foo%"})

# Test 8: compile_br_filter with our_isk_destroyed AND ship_fielded
def test_compile_br_filter_isk_and_ship_fielded():
    from app.analytics.filters import compile_br_filter
    stmt = compile_br_filter({
        "op": "and",
        "clauses": [
            {"field": "our_isk_destroyed", "op": ">=", "value": 50e9},
            {"field": "ship_fielded", "ship": "Lif", "op": ">=", "count": 1, "side": "friendly"},
        ]
    })
    assert stmt is not None

# Test 9: result in [win, tie] filter
def test_compile_br_filter_result_in():
    from app.analytics.filters import compile_br_filter
    stmt = compile_br_filter({"field": "result", "op": "in", "value": ["win", "tie"]})
    assert stmt is not None
    compiled = str(stmt.compile())
    assert "result" in compiled

# Test 10: battle_at range filter
def test_compile_br_filter_battle_at_between():
    from app.analytics.filters import compile_br_filter
    stmt = compile_br_filter({
        "field": "battle_at",
        "op": "between",
        "value": ["2026-01-01T00:00:00", "2026-12-31T23:59:59"]
    })
    assert stmt is not None

# Test 11: POST /api/fights/filter returns 200 + correct fights; 400 on invalid tree
def test_api_filter_fights_200(make_client):
    client = make_client()
    resp = client.post(
        "/api/fights/filter",
        json={"tree": {"field": "isk_destroyed_total", "op": ">=", "value": 0}},
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_api_filter_fights_400_on_invalid_tree(make_client):
    client = make_client()
    resp = client.post(
        "/api/fights/filter",
        json={"tree": {"field": "evil_injection; DROP TABLE fight", "op": ">=", "value": 0}},
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 400

# Test 12: POST /api/brs/filter returns filtered BRs + recomputed summary; 400 on bad tree
def test_api_filter_brs_200(make_client):
    client = make_client()
    resp = client.post(
        "/api/brs/filter",
        json={"tree": {"field": "our_isk_destroyed", "op": ">=", "value": 0}},
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "brs" in body

def test_api_filter_brs_400_on_bad_tree(make_client):
    client = make_client()
    resp = client.post(
        "/api/brs/filter",
        json={"tree": {"field": "nonexistent", "op": "==", "value": 1}},
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 400

# Test 13: POST /api/fights/filter with br_id scopes to that BR's fights
def test_api_filter_fights_with_br_id_scoping(make_client):
    client = make_client()
    # Just verify endpoint accepts br_id param and returns 200
    resp = client.post(
        "/api/fights/filter",
        json={
            "tree": {"field": "isk_destroyed_total", "op": ">=", "value": 0},
            "br_id": "nonexistent-br",
        },
        headers=MEMBER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == []  # No fights for nonexistent BR

# Test 14: Existing endpoints still work after deps refactor
def test_existing_list_brs_still_works(make_client):
    client = make_client()
    resp = client.get("/api/brs", headers=MEMBER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "brs" in body

# Test 15: SQL injection via field name → FilterError (400)
def test_sql_injection_field_raises_filter_error():
    from app.analytics.filters import FilterError, compile_fight_filter
    with pytest.raises(FilterError):
        compile_fight_filter({
            "field": "isk_destroyed_total; DROP TABLE fight",
            "op": ">=",
            "value": 0,
        })

# Test 16: capitals_involved == True filter works
def test_compile_fight_filter_capitals_involved():
    from app.analytics.filters import compile_fight_filter
    stmt = compile_fight_filter({"field": "capitals_involved", "op": "==", "value": True})
    assert stmt is not None
    compiled = str(stmt.compile())
    assert "capitals_involved" in compiled


# Test 17: subcap-only fight stays capitals_involved=False (negative case)
@pytest.mark.asyncio
async def test_backfill_capitals_subcap_only_stays_false(db_session_maker):
    """A fight whose ship types are all sub-capitals must remain capitals_involved=False."""
    from sqlalchemy import select

    from app.db.models import Fight, FightShipCount, InventoryType, SolarSystem
    from app.fights.capitals import backfill_capitals

    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31000099, name="J-SubcapTest", security=None))
        await session.flush()

        # Rifter and Thrasher — neither is a capital
        session.add(InventoryType(
            type_id=587, name="Rifter", group_id=0,
            group_name="Frigate", category_id=0, category_name="Ship",
        ))
        session.add(InventoryType(
            type_id=16242, name="Thrasher", group_id=0,
            group_name="Destroyer", category_id=0, category_name="Ship",
        ))
        await session.flush()

        fight = Fight(
            system_id=31000099,
            started_at=dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
            ended_at=dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC),
            isk_destroyed_total=5e8,
            largest_side_pilots=10,
            capitals_involved=False,
            distinct_alliance_count=2,
        )
        session.add(fight)
        await session.flush()

        session.add(FightShipCount(
            fight_id=fight.fight_id, side_idx=0, ship_type_id=587, count=5,
        ))
        session.add(FightShipCount(
            fight_id=fight.fight_id, side_idx=1, ship_type_id=16242, count=5,
        ))
        await session.commit()

    async with db_session_maker() as session:
        count = await backfill_capitals(session)
        await session.commit()

    async with db_session_maker() as session:
        updated_fight = (await session.execute(select(Fight))).scalar_one()
        assert updated_fight.capitals_involved is False

    assert count == 0


# Test 18: aggregate_br sets capitals_involved=True when a capital is present
@pytest.mark.asyncio
async def test_aggregate_br_sets_capitals_involved_true(db_session_maker):
    """aggregate_br must flag capitals_involved=True when a capital ship appears."""
    import datetime as dt
    import uuid

    from sqlalchemy import select

    from app.db.models import (
        Alliance,
        BattleReport,
        BrKillmail,
        Fight,
        InventoryType,
        Killmail,
        KillmailAttacker,
        SolarSystem,
    )
    from app.fights.aggregate import aggregate_br

    # The capital hull we inject into the DB to ensure name lookup works.
    # type_id=999999 → name="Nidhoggur" (carrier, in capital_ships.json)
    CAPITAL_TYPE_ID = 999999
    CAPITAL_NAME = "Nidhoggur"
    SUB_TYPE_ID = 999998
    SUB_NAME = "Rifter"

    br_id = str(uuid.uuid4())
    our_alliance_id = 88000001
    hostile_alliance_id = 88000002
    km_time = dt.datetime(2026, 6, 10, 20, 15, 0, tzinfo=dt.UTC)

    async with db_session_maker() as session:
        # Solar system
        session.add(SolarSystem(system_id=31099001, name="J-CapTest", security=None))
        await session.flush()

        # Alliance rows (required by FK on Killmail/KillmailAttacker)
        session.add(Alliance(
            alliance_id=our_alliance_id, name="OurAlliance", last_seen_at=km_time,
        ))
        session.add(Alliance(
            alliance_id=hostile_alliance_id, name="HostileAlliance", last_seen_at=km_time,
        ))
        await session.flush()

        # Ship inventory types
        session.add(InventoryType(
            type_id=CAPITAL_TYPE_ID, name=CAPITAL_NAME, group_id=0,
            group_name="Carrier", category_id=0, category_name="Ship",
        ))
        session.add(InventoryType(
            type_id=SUB_TYPE_ID, name=SUB_NAME, group_id=0,
            group_name="Frigate", category_id=0, category_name="Ship",
        ))
        await session.flush()

        # BattleReport row (pending)
        session.add(BattleReport(
            br_id=br_id,
            source="test",
            source_url="https://zkillboard.com/related/31099001/202606101500/",
            source_ref="test-ref",
            title="Capital Test BR",
            created_by_user="test",
            status="pending",
            progress_pct=0,
            created_at=dt.datetime.now(dt.UTC),
        ))
        await session.flush()

        # Killmail: victim loses a Nidhoggur (capital); attacker uses Rifter
        km1 = Killmail(
            killmail_id=80001,
            killmail_time=km_time,
            solar_system_id=31099001,
            victim_character_id=None,
            victim_corporation_id=None,
            victim_alliance_id=our_alliance_id,
            victim_ship_type_id=CAPITAL_TYPE_ID,  # capital victim
            total_value=10_000_000_000.0,
        )
        session.add(km1)
        await session.flush()
        session.add(KillmailAttacker(
            killmail_id=80001,
            attacker_idx=0,
            character_id=None,
            corporation_id=None,
            alliance_id=hostile_alliance_id,
            ship_type_id=SUB_TYPE_ID,
            damage_done=100000,
            final_blow=True,
        ))
        session.add(BrKillmail(br_id=br_id, killmail_id=80001))
        await session.commit()

    async with db_session_maker() as session:
        await aggregate_br(
            session=session,
            br_id=br_id,
            our_alliance_ids=[our_alliance_id],
            our_corp_ids=[],
        )
        await session.commit()

    async with db_session_maker() as session:
        fights = list((await session.execute(select(Fight))).scalars())
        assert len(fights) == 1
        assert fights[0].capitals_involved is True


# Test 19: /api/brs/filter summary is recomputed over the FILTERED subset
@pytest.mark.asyncio
async def test_api_filter_brs_summary_is_subset_summary(tmp_path, monkeypatch):
    """POST /api/brs/filter must compute summary stats over the FILTERED subset only.

    Seed 3 BRs: 2 wins and 1 loss. Apply a filter that selects only the loss BR
    (our_isk_destroyed < threshold that only the loss BR satisfies). The returned
    summary.win_rate must be 0.0 and summary.losses must be 1, not reflecting the
    full set (which would give win_rate=0.666...).
    """
    import datetime as dt

    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", "test-token")
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    base_time = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)

    async with session_maker() as session:
        # BR 1: win, large our_isk_destroyed
        session.add(BattleReport(
            br_id="br-win-1",
            source="test", source_url="https://zkillboard.com/related/1/1/",
            source_ref="ref1", created_by_user="test",
            status="ready", progress_pct=100,
            created_at=base_time, battle_at=base_time,
            result="win", our_isk_destroyed=5_000_000_000.0, our_isk_lost=1_000_000_000.0,
        ))
        # BR 2: win, large our_isk_destroyed
        session.add(BattleReport(
            br_id="br-win-2",
            source="test", source_url="https://zkillboard.com/related/2/2/",
            source_ref="ref2", created_by_user="test",
            status="ready", progress_pct=100,
            created_at=base_time, battle_at=base_time,
            result="win", our_isk_destroyed=4_000_000_000.0, our_isk_lost=500_000_000.0,
        ))
        # BR 3: loss, tiny our_isk_destroyed — only this one passes the filter
        session.add(BattleReport(
            br_id="br-loss-1",
            source="test", source_url="https://zkillboard.com/related/3/3/",
            source_ref="ref3", created_by_user="test",
            status="ready", progress_pct=100,
            created_at=base_time, battle_at=base_time,
            result="loss", our_isk_destroyed=100_000_000.0, our_isk_lost=3_000_000_000.0,
        ))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    from tests.conftest import MEMBER_HEADERS

    with TestClient(app) as client:
        # Filter: only BRs where our_isk_destroyed < 500M (only the loss BR qualifies)
        resp = client.post(
            "/api/brs/filter",
            json={"tree": {"field": "our_isk_destroyed", "op": "<", "value": 500_000_000.0}},
            headers=MEMBER_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "brs" in body

    # The full set has 2 wins + 1 loss; the filtered subset has only 1 loss.
    summary = body["summary"]
    assert summary["total"] == 1
    assert summary["losses"] == 1
    assert summary["wins"] == 0
    assert summary["win_rate"] == 0.0

    # Only the loss BR appears in brs list
    br_ids = [b["br_id"] for b in body["brs"]]
    assert br_ids == ["br-loss-1"]

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
