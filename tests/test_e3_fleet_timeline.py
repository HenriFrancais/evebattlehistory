"""Tests for E3 / redesign: fleet-level timeline analytics + API endpoint.

The fleet timeline aggregates LogEventBucket rows across ALL characters for all
fights in a BR into one series per ``(effect_type, direction)`` pair with data:

  - key         : "{effect_type}:{direction}"  (e.g. "damage:out", "damage:in")
  - effect_type : damage / rep_armor / rep_shield / neut / nos / cap_transfer / scram / disrupt / jam
  - direction   : "out" or "in"
  - metric      : "amount" (abs(sum_amount)) or "count" (event_count, EWAR)
  - values      : per-bucket MAGNITUDE aligned to x (None where no data)

Kills come from FightKill + Killmail + FightSide + InventoryType.
No per-character access gate — visible to all authenticated users.
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


def _series(tl, key):  # type: ignore[no-untyped-def]
    return next((s for s in tl.series if s.key == key), None)


# ---------------------------------------------------------------------------
# Local test helpers (mirror pattern from test_timeline.py)
# ---------------------------------------------------------------------------


async def _make_br_with_fight(session):  # type: ignore[no-untyped-def]
    fight_id = await _insert_fight(
        session,
        victim_char_id=CHAR_A,
        attacker_char_id=CHAR_B,
        started_at=FIGHT_START,
        ended_at=FIGHT_END,
    )
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
# 1. damage:out sums across 2 characters at the same bucket_ts
# ---------------------------------------------------------------------------


async def test_damage_out_sums_across_characters(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 300.0, 3)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "out", 200.0, 2)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    s = _series(tl, "damage:out")
    assert s is not None
    assert s.metric == "amount"
    assert s.direction == "out"
    assert s.values[idx] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# 2. Incoming damage is its own series (damage:in), separate from out
# ---------------------------------------------------------------------------


async def test_incoming_damage_is_separate_series(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 70.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    out = _series(tl, "damage:out")
    inc = _series(tl, "damage:in")
    assert out is not None and inc is not None
    assert out.values[idx] == pytest.approx(100.0)
    assert inc.values[idx] == pytest.approx(70.0)
    assert inc.direction == "in"


# ---------------------------------------------------------------------------
# 3. Alignment: every series values length == len(x), with None gaps
# ---------------------------------------------------------------------------


async def test_alignment_all_series_same_length_as_x(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "scram", "out", 0.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    assert len(tl.x) == 2
    for s in tl.series:
        assert len(s.values) == len(tl.x), f"series {s.key} length mismatch"

    ts1_idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ts2_idx = tl.x.index(int(BUCKET_TS_2.timestamp()))

    dmg = _series(tl, "damage:out")
    assert dmg.values[ts1_idx] is not None
    assert dmg.values[ts2_idx] is None

    scram = _series(tl, "scram:out")
    assert scram.values[ts1_idx] is None
    assert scram.values[ts2_idx] is not None


# ---------------------------------------------------------------------------
# 4. rep_armor and rep_shield are distinct series; reps keep direction
# ---------------------------------------------------------------------------


async def test_reps_are_per_effect_series(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep_armor", "out", 400.0, 4)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "rep_shield", "out", 150.0, 3)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    assert _series(tl, "rep_armor:out").values[idx] == pytest.approx(400.0)
    assert _series(tl, "rep_shield:out").values[idx] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# 5. EWAR effects are count-based (event_count, not sum_amount)
# ---------------------------------------------------------------------------


async def test_ewar_uses_event_count_not_sum_amount(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # scram: event_count=7, sum_amount=9999 (ignored for count metric)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "scram", "out",
                             sum_amount=9999.0, event_count=7)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    s = _series(tl, "scram:out")
    assert s.metric == "count"
    assert s.values[idx] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# 6. Cap warfare magnitude uses abs(sum_amount) (sign in logs is inconsistent)
# ---------------------------------------------------------------------------


async def test_cap_warfare_uses_abs_magnitude(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # nos incoming recorded as negative in real logs — magnitude is abs.
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "nos", "in", -250.0, 2)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    s = _series(tl, "nos:in")
    assert s.metric == "amount"
    assert s.values[idx] == pytest.approx(250.0)


# ---------------------------------------------------------------------------
# 7. kills list has correct entries
# ---------------------------------------------------------------------------


async def test_kills_list_correct_entries(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    km_time = FIGHT_START + dt.timedelta(seconds=60)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
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

    our_kill = next((k for k in tl.kills if k.killmail_id == km_id), None)
    assert our_kill is not None
    assert our_kill.victim_character_id == CHAR_B
    assert our_kill.victim_ship_name == "TestShip"
    # No alliance/corp on the victim and no baseline/overrides → unassigned.
    assert our_kill.side_kind == "unassigned"
    assert our_kill.isk == pytest.approx(500_000_000.0)
    assert our_kill.ts == int(km_time.timestamp())
    assert tl.kills == sorted(tl.kills, key=lambda k: k.ts)


# ---------------------------------------------------------------------------
# 8. Empty BR → empty series + empty kills, no error
# ---------------------------------------------------------------------------


async def test_empty_br_no_logs_no_kills(db_session_maker) -> None:  # type: ignore[no-untyped-def]
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
    assert tl.series == []
    assert tl.kills == []
    assert tl.t_start is None
    assert tl.t_end is None


# ---------------------------------------------------------------------------
# Unknown / non-directional buckets are excluded from series
# ---------------------------------------------------------------------------


async def test_unknown_effect_buckets_excluded(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # '' effect / '' direction (NULL→"" convention) must not produce a series.
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "", "", 0.0, 3)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "damage", "out", 100.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    keys = {s.key for s in tl.series}
    assert keys == {"damage:out"}
    # x only includes the relevant bucket
    assert tl.x == [int(BUCKET_TS_2.timestamp())]


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------


from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN  # noqa: E402


@pytest.mark.asyncio
async def test_api_fleet_timeline_returns_correct_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 90.0, 2)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=CREATOR_HEADERS)

    assert resp.status_code == 200
    data = resp.json()

    for field in ("x", "series", "kills", "bucket_seconds", "fights", "t_start", "t_end"):
        assert field in data
    assert data["bucket_seconds"] == 5

    keys = {s["key"] for s in data["series"]}
    assert {"damage:out", "damage:in"} <= keys
    for s in data["series"]:
        assert len(s["values"]) == len(data["x"]), f"series {s['key']} alignment broken"
        assert "effect_type" in s and "direction" in s and "metric" in s

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_fleet_timeline_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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


@pytest.mark.asyncio
async def test_api_fleet_timeline_accessible_by_member(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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
# Contributions: source→target breakdown within a bucket, sorted desc
# ---------------------------------------------------------------------------


async def test_fleet_contributions_source_target(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_contributions
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="hh", mime="text/plain", size=1,
                         parse_status="parsed", event_count=3,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        ts = BUCKET_TS_1  # 2026-06-10 20:00:00 UTC
        # Pilot A → Enemy1 damage 300; A → Enemy1 damage 100; A → Enemy2 damage 50
        for other, amt in (("Enemy1", 300.0), ("Enemy1", 100.0), ("Enemy2", 50.0)):
            session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=ts,
                                 effect_type="damage", direction="out", amount=amt,
                                 other_name=other, fight_id=fight_id))
        await session.commit()

    from app.config import get_settings

    async with db_session_maker() as session:
        rows = await fleet_contributions(session, br_id, int(ts.timestamp()), get_settings())

    assert rows, "expected contribution rows"
    top = rows[0]
    assert top.source_character_id == CHAR_A
    assert top.target_name == "Enemy1"
    assert top.group == "damage"
    assert top.value == pytest.approx(400.0)  # 300 + 100 merged
    # sorted most→least
    assert [r.value for r in rows] == sorted((r.value for r in rows), reverse=True)


async def test_clean_target_name_strips_tags() -> None:
    from app.analytics.fleet import _clean_target_name

    assert _clean_target_name("Proteus Nate Marston [NVACA] &lt;NV&gt;") == "Proteus Nate Marston"
    assert _clean_target_name("Tsawind") == "Tsawind"
    assert _clean_target_name("Name [CORP] <ALLI>") == "Name"
