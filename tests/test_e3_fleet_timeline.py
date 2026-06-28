"""Tests for E3 / redesign: fleet-level timeline analytics + API endpoint.

The fleet timeline aggregates LogEventBucket rows across ALL characters for all
fights in a BR into one series per ``(effect_type, direction)`` pair with data:

  - key         : "{effect_type}:{direction}"  (e.g. "damage:out", "damage:in")
  - effect_type : damage / rep_armor / rep_shield / neut / nos / cap_transfer /
                  scram / disrupt / jam
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

    from app.analytics.fleet import fleet_snapshot
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
        frm = int(ts.timestamp())
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    assert rows, "expected contribution rows"
    top = rows[0]
    assert top.source_character_id == CHAR_A
    assert top.target_name == "Enemy1"
    assert top.group == "damage"
    assert top.value == pytest.approx(400.0)  # 300 + 100 merged
    # sorted most→least
    assert [r.value for r in rows] == sorted((r.value for r in rows), reverse=True)


async def test_fleet_snapshot_character_id_scopes_to_one_pilot(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """fleet_snapshot(character_id=…) returns only that character's log perspective."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        ts = BUCKET_TS_1
        for cid, sha in ((CHAR_A, "aa"), (CHAR_B, "bb")):
            gf = GamelogFile(uploaded_by_user="u", claimed_character_id=cid,
                             resolved_via="filename",
                             stored_path=f"/{sha}", sha256=sha, mime="text/plain", size=1,
                             parse_status="parsed", event_count=1,
                             uploaded_at=_dt.datetime.now(_dt.UTC))
            session.add(gf)
            await session.flush()
            session.add(LogEvent(file_id=gf.file_id, character_id=cid, ts=ts,
                                 effect_type="damage", direction="out", amount=100.0,
                                 other_name=f"Enemy-of-{cid}", fight_id=fight_id))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        all_rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())
        a_rows = await fleet_snapshot(
            session, br_id, frm, frm + 1, get_settings(), character_id=CHAR_A)

    assert {r.source_character_id for r in all_rows} == {CHAR_A, CHAR_B}
    assert {r.source_character_id for r in a_rows} == {CHAR_A}


async def test_clean_target_name_strips_tags() -> None:
    from app.analytics.fleet import _clean_target_name

    assert _clean_target_name("Proteus Nate Marston [NVACA] &lt;NV&gt;") == "Proteus Nate Marston"
    assert _clean_target_name("Tsawind") == "Tsawind"
    assert _clean_target_name("Name [CORP] <ALLI>") == "Name"


async def test_contributions_damage_row_has_weapon_icon(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import GamelogFile, InventoryType, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # SDE row so the exact module name resolves to a type_id.
        session.add(InventoryType(type_id=3174, name="250mm Railgun II"))
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="zz", mime="text/plain", size=1,
                         parse_status="parsed", event_count=1,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        ts = BUCKET_TS_1
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=ts,
                             effect_type="damage", direction="out", amount=400.0,
                             other_name="Enemy1", module_name="250mm Railgun II",
                             fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        frm = int(ts.timestamp())
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    dmg = next(r for r in rows if r.target_name == "Enemy1")
    assert dmg.module_name == "250mm Railgun II"
    assert dmg.icon_type_id == 3174
    assert dmg.weapon_category == "hybrid"


async def test_contributions_non_damage_row_has_no_weapon(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="yy", mime="text/plain", size=1,
                         parse_status="parsed", event_count=1,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        ts = BUCKET_TS_1
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=ts,
                             effect_type="rep_armor", direction="out", amount=500.0,
                             other_name="Friend1", module_name="Large Remote Armor Repairer II",
                             fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        frm = int(ts.timestamp())
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    rep = next(r for r in rows if r.target_name == "Friend1")
    assert rep.module_name is None
    assert rep.icon_type_id is None
    assert rep.weapon_category is None


async def test_fleet_snapshot_range_ship_and_quality(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="qq", mime="text/plain", size=1,
                         parse_status="parsed", event_count=3,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        t0 = BUCKET_TS_1                       # 20:00:00
        t1 = BUCKET_TS_2                       # 20:00:05
        t_out = t1 + _dt.timedelta(seconds=30)  # outside the window
        # Two damage hits on Enemy1 (Loki) with differing quality + one outside-range hit.
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t0, effect_type="damage",
                             direction="out", amount=300.0, quality="Smashes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t1, effect_type="damage",
                             direction="out", amount=100.0, quality="Smashes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t_out,
                             effect_type="damage",
                             direction="out", amount=999.0, quality="Grazes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        await session.commit()

    frm = int(t0.timestamp())
    to = int(t1.timestamp()) + 1
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, to, get_settings())

    enemy = next(r for r in rows if r.target_name == "Enemy1")
    assert enemy.target_ship == "Loki"
    assert enemy.value == pytest.approx(400.0)   # 300 + 100; the 999 is outside the range
    assert enemy.quality == "Smashes"            # dominant quality


async def test_kill_has_victim_character_name(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.db.models import Character

    victim_id = 2300000001  # fresh id; CHAR_A/CHAR_B already exist via _insert_fight
    km_time = FIGHT_START + dt.timedelta(seconds=60)
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        session.add(Character(character_id=victim_id, name="Mara Sant",
                              last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        await _insert_killmail(session, fight_id=fight_id, side_idx=1, victim_char_id=victim_id,
                               ship_type_id=_SHIP_TYPE_ID, total_value=1.0, killmail_time=km_time)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    k = next(k for k in tl.kills if k.victim_character_id == victim_id)
    assert k.victim_character_name == "Mara Sant"


async def test_kill_unknown_victim_name_is_none(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.db.models import Character

    km_time = FIGHT_START + dt.timedelta(seconds=90)
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # Character row exists but has no name → must resolve to None gracefully.
        session.add(Character(character_id=777000777, name=None,
                              last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        await _insert_killmail(session, fight_id=fight_id, side_idx=1, victim_char_id=777000777,
                               ship_type_id=_SHIP_TYPE_ID, total_value=1.0, killmail_time=km_time)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    k = next(k for k in tl.kills if k.victim_character_id == 777000777)
    assert k.victim_character_name is None


# ---------------------------------------------------------------------------
# Task 11: per-bucket leaders
# ---------------------------------------------------------------------------


async def test_leaders_per_bucket_picks_max_character(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # _make_br_with_fight creates Character rows for CHAR_A/CHAR_B via _insert_fight;
        # merge (upsert) so we can rename them for the assertion.
        await session.merge(
            Character(character_id=CHAR_A, name="Alice", last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.merge(
            Character(character_id=CHAR_B, name="Bob", last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.flush()
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "in", 300.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 500.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "out", 50.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep_armor", "in", 200.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "rep_shield", "out", 150.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]
    # No killmail alliance data → both chars unknown-side (hostile); with no friendly
    # OUTGOING damage seeded, every receiver metric is None.
    assert ld.top_friendly_dmg_taken is None
    assert ld.top_hostile_dmg_taken is None
    assert ld.top_friendly_rep_recv is None


async def test_leaders_aligned_to_x_and_null_when_absent(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())

    assert len(tl.leaders) == len(tl.x)
    ld = tl.leaders[tl.x.index(int(BUCKET_TS_1.timestamp()))]
    # damage:out bucket only — all 3 incoming-damage/rep fields are None
    assert ld.top_friendly_dmg_taken is None
    assert ld.top_hostile_dmg_taken is None
    assert ld.top_friendly_rep_recv is None


async def test_leaders_empty_for_no_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, _ = await _make_br_with_fight(session)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())

    assert tl.leaders == []


# ---------------------------------------------------------------------------
# Task 1 (side-aware): per-bucket leaders split by target's side
# ---------------------------------------------------------------------------

# Alliance IDs used in side-aware tests:
_FRIENDLY_ALLI = 99006113   # NV baseline — always friendly
_HOSTILE_ALLI  = 88888888   # not in baseline → hostile


async def test_side_aware_leaders_friendly_dmg_taken(db_session_maker) -> None:
    """top_friendly_dmg_taken = FRIENDLY char with max incoming damage.
    top_hostile_dmg_taken = HOSTILE char with max incoming damage.
    top_friendly_rep_recv = FRIENDLY char with max incoming reps.
    Unknown-side chars are treated as HOSTILE."""
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character, Killmail, KillmailAttacker

    # Four characters:
    #   CHAR_F1 (friendly, alliance_id=99006113) — more dmg taken than CHAR_F2
    #   CHAR_F2 (friendly, alliance_id=99006113) — less dmg taken; more reps recv than CHAR_F1
    #   CHAR_H1 (hostile,  alliance_id=88888888) — highest hostile dmg taken
    #   CHAR_H2 (hostile,  alliance_id=88888888) — less dmg taken
    CHAR_F1 = 3100000001
    CHAR_F2 = 3100000002
    CHAR_H1 = 3200000001
    CHAR_H2 = 3200000002

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)

        # Create Alliance rows for FK constraints
        from app.db.models import Alliance
        session.add(Alliance(alliance_id=_FRIENDLY_ALLI, name="Friendly Alliance",
                             last_seen_at=dt.datetime.now(dt.UTC)))
        session.add(Alliance(alliance_id=_HOSTILE_ALLI, name="Hostile Alliance",
                             last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()

        # Create Character rows so names resolve
        for cid, name in (
            (CHAR_F1, "FriendlyOne"), (CHAR_F2, "FriendlyTwo"),
            (CHAR_H1, "HostileOne"), (CHAR_H2, "HostileTwo"),
        ):
            await session.merge(
                Character(character_id=cid, name=name,
                          last_seen_at=dt.datetime.now(dt.UTC))
            )
        await session.flush()

        # Killmail to establish alliance membership via KillmailAttacker rows.
        # Use unique killmail_id values that won't collide with fight's own KMs.
        km_base = 9_000_000
        for i, (cid, alli) in enumerate(
            [(CHAR_F1, _FRIENDLY_ALLI), (CHAR_F2, _FRIENDLY_ALLI),
             (CHAR_H1, _HOSTILE_ALLI), (CHAR_H2, _HOSTILE_ALLI)]
        ):
            km_id = km_base + i
            # Minimal Killmail row needed for FightKill FK
            session.add(Killmail(
                killmail_id=km_id,
                killmail_time=FIGHT_START,
                solar_system_id=31002222,
                victim_character_id=None,
                victim_ship_type_id=_SHIP_TYPE_ID,
                total_value=0.0,
                npc_kill=False,
                solo_kill=False,
            ))
            await session.flush()
            from app.db.models import FightKill
            session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
            await session.flush()
            # Attacker row that carries alliance_id → side classification
            session.add(KillmailAttacker(
                killmail_id=km_id,
                attacker_idx=0,
                character_id=cid,
                corporation_id=None,
                alliance_id=alli,
                ship_type_id=_SHIP_TYPE_ID,
                weapon_type_id=None,
                damage_done=0,
                final_blow=False,
                security_status=0.0,
            ))
        await session.flush()

        # Log buckets: incoming damage for both sides; incoming reps for friendly
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "damage", "in", 500.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F2, BUCKET_TS_1, "damage", "in", 200.0, 1)
        await _insert_bucket(session, fight_id, CHAR_H1, BUCKET_TS_1, "damage", "in", 800.0, 1)
        await _insert_bucket(session, fight_id, CHAR_H2, BUCKET_TS_1, "damage", "in", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F2, BUCKET_TS_1, "rep_armor", "in", 300.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "rep_armor", "in", 100.0, 1)
        # Hostile receivers come from friendly OUTGOING damage by target (other_name).
        await _insert_out_dmg(session, fight_id, CHAR_F1, "HostileOne", 800.0, BUCKET_TS_1, "od1")
        await _insert_out_dmg(session, fight_id, CHAR_F1, "HostileTwo", 100.0, BUCKET_TS_1, "od2")
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(
            session, br_id,
            our_alliance_ids=[_FRIENDLY_ALLI],
            settings=get_settings(),
        )

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]

    # Friendly char with most incoming damage
    assert ld.top_friendly_dmg_taken is not None
    assert ld.top_friendly_dmg_taken.name == "FriendlyOne"
    assert ld.top_friendly_dmg_taken.amount == pytest.approx(500.0)

    # Hostile char with most incoming damage
    assert ld.top_hostile_dmg_taken is not None
    assert ld.top_hostile_dmg_taken.name == "HostileOne"
    assert ld.top_hostile_dmg_taken.amount == pytest.approx(800.0)

    # Friendly char with most incoming reps
    assert ld.top_friendly_rep_recv is not None
    assert ld.top_friendly_rep_recv.name == "FriendlyTwo"
    assert ld.top_friendly_rep_recv.amount == pytest.approx(300.0)


async def test_side_aware_leaders_null_when_no_friendly(db_session_maker) -> None:
    """When only hostile chars have log data, top_friendly_* fields are None."""
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character, FightKill, Killmail, KillmailAttacker

    CHAR_H1 = 3300000001

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # Create Alliance rows for FK constraints
        from app.db.models import Alliance
        session.add(Alliance(alliance_id=_FRIENDLY_ALLI, name="Friendly Alliance",
                             last_seen_at=dt.datetime.now(dt.UTC)))
        session.add(Alliance(alliance_id=_HOSTILE_ALLI, name="Hostile Alliance",
                             last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        await session.merge(
            Character(character_id=CHAR_H1, name="HostileOnly",
                      last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.flush()

        km_id = 9_100_000
        session.add(Killmail(
            killmail_id=km_id, killmail_time=FIGHT_START,
            solar_system_id=31002222, victim_character_id=None,
            victim_ship_type_id=_SHIP_TYPE_ID, total_value=0.0,
            npc_kill=False, solo_kill=False,
        ))
        await session.flush()
        session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
        await session.flush()
        session.add(KillmailAttacker(
            killmail_id=km_id, attacker_idx=0, character_id=CHAR_H1, corporation_id=None,
            alliance_id=_HOSTILE_ALLI, ship_type_id=_SHIP_TYPE_ID,
            weapon_type_id=None, damage_done=0, final_blow=False, security_status=0.0,
        ))
        await session.flush()

        await _insert_bucket(session, fight_id, CHAR_H1, BUCKET_TS_1, "damage", "in", 400.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(
            session, br_id,
            our_alliance_ids=[_FRIENDLY_ALLI],
            settings=get_settings(),
        )

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]
    # Only a hostile char's incoming bucket, no friendly OUTGOING damage → no
    # receiver metric populates (hostile-taken is read from friendly outgoing damage).
    assert ld.top_friendly_dmg_taken is None
    assert ld.top_hostile_dmg_taken is None
    assert ld.top_friendly_rep_recv is None


# ---------------------------------------------------------------------------
# Task 12: API endpoint exposes leaders[]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_fleet_timeline_includes_leaders(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/fleet-timeline has leaders[] aligned to x[],
    with top_hostile_dmg_taken populated (CHAR_A unknown-side → hostile)."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import Character
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
        # Name the character so we can assert on name in the response.
        await session.merge(
            Character(character_id=CHAR_A, name="Alice", last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.flush()
        # A bucket establishes the x-axis. Alice is unknown-side → hostile, so her
        # incoming bucket yields no friendly metric; her "taken" damage comes from
        # our OUTGOING damage aimed at her (other_name).
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 400.0, 4)
        await _insert_out_dmg(session, fight_id, CHAR_A, "Alice", 400.0, BUCKET_TS_1, "oda")
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/fleet-timeline", headers=CREATOR_HEADERS)

    assert resp.status_code == 200
    data = resp.json()

    assert "leaders" in data, "response missing 'leaders' key"
    assert len(data["leaders"]) == len(data["x"]), (
        f"leaders length {len(data['leaders'])} != x length {len(data['x'])}"
    )

    bucket_idx = data["x"].index(int(BUCKET_TS_1.timestamp()))
    ld = data["leaders"][bucket_idx]

    # Empty leader slots are omitted from the payload (not sent as null) to shrink
    # it; an absent key therefore means "no leader", same as the client reads it.
    assert ld.get("top_friendly_dmg_taken") is None
    assert ld["top_hostile_dmg_taken"] is not None
    assert ld["top_hostile_dmg_taken"]["name"] == "Alice"
    assert ld["top_hostile_dmg_taken"]["amount"] == pytest.approx(400.0)
    assert ld.get("top_friendly_rep_recv") is None

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# Tackle-fix tests: fleet_snapshot scram/disrupt use deduped source_name path
# ---------------------------------------------------------------------------


async def _make_gamelog_file(session, char_id: int, sha: str):  # type: ignore[no-untyped-def]
    """Insert a minimal GamelogFile and return it (flushed, not committed)."""
    import datetime as _dt
    from app.db.models import GamelogFile  # isort:skip
    gf = GamelogFile(
        uploaded_by_user="u",
        claimed_character_id=char_id,
        resolved_via="filename",
        stored_path=f"/{sha}",
        sha256=sha,
        mime="text/plain",
        size=1,
        parse_status="parsed",
        event_count=1,
        uploaded_at=_dt.datetime.now(_dt.UTC),
    )
    session.add(gf)
    await session.flush()
    return gf


async def _insert_out_dmg(  # type: ignore[no-untyped-def]
    session, fight_id, owner_cid, target_name, amount, ts, sha
):
    """Insert a friendly OUTGOING damage LogEvent aimed at *target_name*.

    top_hostile_dmg_taken is computed from outgoing damage aggregated by target,
    so hostile-receiver tests seed these rows (the target must be a hostile
    killmail participant with a Character row so its name resolves)."""
    from app.db.models import LogEvent  # isort:skip
    gf = await _make_gamelog_file(session, owner_cid, sha)
    session.add(LogEvent(
        file_id=gf.file_id, character_id=owner_cid, ts=ts,
        effect_type="damage", direction="out", amount=amount,
        other_name=target_name, fight_id=fight_id,
    ))
    await session.flush()


async def test_snapshot_tackle_deduped_single_contribution(db_session_maker) -> None:
    """Deduped scram rows produce exactly ONE Contribution (source_name->target_name),
    NOT a friendly-on-friendly owner-attributed row per the old logic."""
    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import LogEvent

    OWNER1 = 4100000001  # a bystander log owner
    OWNER2 = 4100000002  # another bystander log owner
    OWNER3 = 4100000003  # authoritative observer

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        ts = BUCKET_TS_1

        # Authoritative survivor row (dedupe_suppressed=False): real tackle
        gf1 = await _make_gamelog_file(session, OWNER3, "tackle-auth")
        session.add(LogEvent(
            file_id=gf1.file_id, character_id=OWNER3, ts=ts,
            effect_type="scram", direction="out", amount=0.0,
            other_name="Victim", fight_id=fight_id,
            source_name="Tackler", target_name="Victim",
            authoritative=True, dedupe_suppressed=False,
        ))

        # Three suppressed duplicates from other observers
        for sha, owner in (("tackle-b1", OWNER1), ("tackle-b2", OWNER2), ("tackle-b3", OWNER3)):
            gf = await _make_gamelog_file(session, owner, sha)
            session.add(LogEvent(
                file_id=gf.file_id, character_id=owner, ts=ts,
                effect_type="scram", direction="out", amount=0.0,
                other_name="Victim", fight_id=fight_id,
                source_name="Tackler", target_name="Victim",
                authoritative=False, dedupe_suppressed=True,
            ))

        # Old-style owner-attributed noise row (what the broken path used to produce).
        # source_name=None means not pilot-resolved -- ship-only/NPC party; excluded by new logic.
        gf_noise = await _make_gamelog_file(session, OWNER1, "tackle-noise")
        session.add(LogEvent(
            file_id=gf_noise.file_id, character_id=OWNER1, ts=ts,
            effect_type="scram", direction="out", amount=0.0,
            other_name="Friend", fight_id=fight_id,
            source_name=None, target_name=None,
            authoritative=False, dedupe_suppressed=False,
        ))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    tackle_rows = [r for r in rows if r.effect_type in ("scram", "disrupt")]

    # Exactly one tackle Contribution
    assert len(tackle_rows) == 1, (
        f"Expected 1 tackle row, got {len(tackle_rows)}: "
        f"{[(r.source_name, r.target_name, r.effect_type) for r in tackle_rows]}"
    )
    c = tackle_rows[0]
    assert c.source_name == "Tackler"
    assert c.target_name == "Victim"
    assert c.source_character_id is None      # pilot-resolved path has no char_id
    assert c.group == "ewar"
    assert c.direction == "out"
    assert c.value == 1                       # 1 deduped row = 1 event


async def test_snapshot_tackle_null_source_excluded(db_session_maker) -> None:
    """Tackle rows where source_name IS NULL (ship-only/NPC) do NOT appear."""
    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        ts = BUCKET_TS_1
        gf = await _make_gamelog_file(session, CHAR_A, "tackle-null-src")
        session.add(LogEvent(
            file_id=gf.file_id, character_id=CHAR_A, ts=ts,
            effect_type="scram", direction="out", amount=0.0,
            other_name="Victim", fight_id=fight_id,
            source_name=None, target_name=None,
            authoritative=False, dedupe_suppressed=False,
        ))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    tackle_rows = [r for r in rows if r.effect_type in ("scram", "disrupt")]
    assert tackle_rows == [], (
        f"Expected no tackle rows for NULL source, got {tackle_rows}"
    )


async def test_snapshot_damage_still_aggregates_by_owner(db_session_maker) -> None:
    """Regression: non-tackle effects (damage) are still owner-attributed as before."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import Character, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        ts = BUCKET_TS_1
        await session.merge(
            Character(character_id=CHAR_A, name="DmgPilot",
                      last_seen_at=_dt.datetime.now(_dt.UTC))
        )
        await session.flush()
        gf = await _make_gamelog_file(session, CHAR_A, "dmg-regression")
        session.add(LogEvent(
            file_id=gf.file_id, character_id=CHAR_A, ts=ts,
            effect_type="damage", direction="out", amount=500.0,
            other_name="Enemy1", fight_id=fight_id,
            source_name=None, target_name=None,
            dedupe_suppressed=False,
        ))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    dmg_rows = [r for r in rows if r.effect_type == "damage"]
    assert len(dmg_rows) == 1
    assert dmg_rows[0].source_character_id == CHAR_A
    assert dmg_rows[0].source_name == "DmgPilot"
    assert dmg_rows[0].target_name == "Enemy1"
    assert dmg_rows[0].value == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Logistics dedup: a single physical rep tick is logged by BOTH the repper
# (out) and the recipient (in). The snapshot must count it ONCE per
# (repper -> recipient), not twice — mirroring composition._reps_applied_by_char.
# ---------------------------------------------------------------------------


async def test_snapshot_reps_deduped_across_in_out(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """When both the logi's outgoing log and the recipient's incoming log are
    present, the same rep tick must produce ONE Contribution, not two, and the
    total must not double-count."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import Character, LogEvent

    REPPER = 2100000001   # CHAR_A
    RECIP = 2200000001    # CHAR_B

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # Give both pilots resolvable names (matching is by resolved name).
        await session.merge(Character(character_id=REPPER, name="Logi Pilot",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.merge(Character(character_id=RECIP, name="Tackled Friend",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.flush()
        ts = BUCKET_TS_1

        # Two physical rep ticks the logi applied to the recipient. Each is
        # logged twice: once by the repper (out, other_name=recipient) and once
        # by the recipient (in, other_name=repper). Same server-second + amount.
        gf_repper = await _make_gamelog_file(session, REPPER, "reps-out")
        gf_recip = await _make_gamelog_file(session, RECIP, "reps-in")
        for amt in (400.0, 600.0):
            session.add(LogEvent(
                file_id=gf_repper.file_id, character_id=REPPER, ts=ts,
                effect_type="rep_armor", direction="out", amount=amt,
                other_name="Tackled Friend", fight_id=fight_id))
            session.add(LogEvent(
                file_id=gf_recip.file_id, character_id=RECIP, ts=ts,
                effect_type="rep_armor", direction="in", amount=amt,
                other_name="Logi Pilot", fight_id=fight_id))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    rep_rows = [r for r in rows if r.effect_type == "rep_armor"]
    # Exactly one Contribution for the logi -> recipient rep, counted once.
    assert len(rep_rows) == 1, (
        f"expected 1 deduped rep row, got {len(rep_rows)}: "
        f"{[(r.source_name, r.target_name, r.direction, r.value) for r in rep_rows]}"
    )
    c = rep_rows[0]
    assert c.source_character_id == REPPER
    assert c.source_name == "Logi Pilot"
    assert c.target_name == "Tackled Friend"
    assert c.value == pytest.approx(1000.0)  # 400 + 600, NOT doubled to 2000


async def test_snapshot_reps_uploading_repper_is_authoritative(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A repper who uploaded their own log is summed from THAT log only — recipient
    copies are ignored, even when a recipient logged more ticks. This is what stops
    the clock-skew / overheal double counting (a recipient's "repaired by X" lands a
    second off and/or with a different effective amount, so per-tick matching fails)."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import Character, LogEvent

    REPPER = 2100000001
    RECIP = 2200000001

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(Character(character_id=REPPER, name="Logi Pilot",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.merge(Character(character_id=RECIP, name="Tackled Friend",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.flush()
        ts = BUCKET_TS_1
        gf_repper = await _make_gamelog_file(session, REPPER, "reps-out-p")
        gf_recip = await _make_gamelog_file(session, RECIP, "reps-in-p")
        # Repper's OWN log: one rep of 400 (this is the authoritative record).
        session.add(LogEvent(file_id=gf_repper.file_id, character_id=REPPER, ts=ts,
                             effect_type="rep_armor", direction="out", amount=400.0,
                             other_name="Tackled Friend", fight_id=fight_id))
        # Recipient's log: copies of the repper's reps, clock-skewed / extra (600).
        # These must be ignored because the repper uploaded their own log.
        session.add(LogEvent(file_id=gf_recip.file_id, character_id=RECIP, ts=ts,
                             effect_type="rep_armor", direction="in", amount=400.0,
                             other_name="Logi Pilot", fight_id=fight_id))
        session.add(LogEvent(file_id=gf_recip.file_id, character_id=RECIP, ts=ts,
                             effect_type="rep_armor", direction="in", amount=600.0,
                             other_name="Logi Pilot", fight_id=fight_id))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    rep_rows = [r for r in rows if r.effect_type == "rep_armor"]
    assert len(rep_rows) == 1, (
        f"expected 1 rep row, got {len(rep_rows)}: "
        f"{[(r.source_name, r.target_name, r.direction, r.value) for r in rep_rows]}"
    )
    c = rep_rows[0]
    assert c.source_name == "Logi Pilot"
    assert c.target_name == "Tackled Friend"
    assert c.value == pytest.approx(400.0)  # repper's own log only, not 400+600


async def test_snapshot_reps_in_only_kept_when_repper_did_not_log(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """An incoming rep tick with no matching outgoing tick (the repper never
    uploaded a log) must still be counted — dedup suppresses duplicates only."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import Character, LogEvent

    REPPER = 2100000001
    RECIP = 2200000001

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(Character(character_id=REPPER, name="Logi Pilot",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.merge(Character(character_id=RECIP, name="Tackled Friend",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.flush()
        ts = BUCKET_TS_1
        # Only the recipient's incoming log exists.
        gf_recip = await _make_gamelog_file(session, RECIP, "reps-in-only")
        session.add(LogEvent(
            file_id=gf_recip.file_id, character_id=RECIP, ts=ts,
            effect_type="rep_armor", direction="in", amount=750.0,
            other_name="Logi Pilot", fight_id=fight_id))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    rep_rows = [r for r in rows if r.effect_type == "rep_armor"]
    assert len(rep_rows) == 1
    assert rep_rows[0].value == pytest.approx(750.0)


async def test_snapshot_cap_transfer_deduped_across_in_out(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Remote cap transfer is bilaterally logged just like reps: the transmitter
    logs it (out, other_name=recipient) and the recipient logs it (in,
    other_name=transmitter). The same physical tick must collapse to ONE
    Contribution counted once, not two rows that double the total."""
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import Character, LogEvent

    XMIT = 2100000001    # CHAR_A — the one transmitting cap
    RECIP = 2200000001   # CHAR_B — the one receiving cap

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(Character(character_id=XMIT, name="Cap Pilot",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.merge(Character(character_id=RECIP, name="Thirsty Friend",
                                      last_seen_at=_dt.datetime.now(_dt.UTC)))
        await session.flush()
        ts = BUCKET_TS_1

        gf_xmit = await _make_gamelog_file(session, XMIT, "cap-out")
        gf_recip = await _make_gamelog_file(session, RECIP, "cap-in")
        for amt in (300.0, 500.0):
            session.add(LogEvent(
                file_id=gf_xmit.file_id, character_id=XMIT, ts=ts,
                effect_type="cap_transfer", direction="out", amount=amt,
                other_name="Thirsty Friend", fight_id=fight_id))
            session.add(LogEvent(
                file_id=gf_recip.file_id, character_id=RECIP, ts=ts,
                effect_type="cap_transfer", direction="in", amount=amt,
                other_name="Cap Pilot", fight_id=fight_id))
        await session.commit()

    frm = int(ts.timestamp())
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, frm + 1, get_settings())

    cap_rows = [r for r in rows if r.effect_type == "cap_transfer"]
    assert len(cap_rows) == 1, (
        f"expected 1 deduped cap_transfer row, got {len(cap_rows)}: "
        f"{[(r.source_name, r.target_name, r.direction, r.value) for r in cap_rows]}"
    )
    c = cap_rows[0]
    assert c.source_character_id == XMIT
    assert c.source_name == "Cap Pilot"
    assert c.target_name == "Thirsty Friend"
    assert c.direction == "out"  # canonical transmitter -> recipient
    assert c.value == pytest.approx(800.0)  # 300 + 500, NOT doubled to 1600


async def test_cap_and_tackle_leaders(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """The cap panel exposes hostile-neut-taken / friendly-neut-dealt / friendly-cap-recv;
    the ewar panel exposes hostile- and friendly-tackle-taken (counts)."""
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import (
        Alliance,
        Character,
        FightKill,
        Killmail,
        KillmailAttacker,
        LogEvent,
    )

    CHAR_F1 = 3100000011  # friendly — applies neut, lands tackle
    CHAR_F2 = 3100000012  # friendly — receives cap + receives tackle
    CHAR_H1 = 3200000011  # hostile  — takes neut + takes tackle
    now = dt.datetime.now(dt.UTC)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        session.add(Alliance(alliance_id=_FRIENDLY_ALLI, name="F", last_seen_at=now))
        session.add(Alliance(alliance_id=_HOSTILE_ALLI, name="H", last_seen_at=now))
        await session.flush()
        for cid, name in ((CHAR_F1, "NeutBro"), (CHAR_F2, "CapMe"), (CHAR_H1, "HostileOne")):
            await session.merge(Character(character_id=cid, name=name, last_seen_at=now))
        await session.flush()
        # Side classification via killmail attacker alliance.
        for i, (cid, alli) in enumerate(
            [(CHAR_F1, _FRIENDLY_ALLI), (CHAR_F2, _FRIENDLY_ALLI), (CHAR_H1, _HOSTILE_ALLI)]
        ):
            km_id = 9_100_000 + i
            session.add(Killmail(killmail_id=km_id, killmail_time=FIGHT_START,
                                 solar_system_id=31002222, victim_character_id=None,
                                 victim_ship_type_id=_SHIP_TYPE_ID, total_value=0.0,
                                 npc_kill=False, solo_kill=False))
            await session.flush()
            session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
            await session.flush()
            session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=0, character_id=cid,
                                         corporation_id=None, alliance_id=alli,
                                         ship_type_id=_SHIP_TYPE_ID, weapon_type_id=None,
                                         damage_done=0, final_blow=False, security_status=0.0))
        await session.flush()

        # Friendly cap pressure applied (by source) = neut 4200 + nos 800 = 5000.
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "neut", "out", 4200.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "nos", "out", 800.0, 1)
        # Friendly cap received (by receiver).
        await _insert_bucket(
            session, fight_id, CHAR_F2, BUCKET_TS_1, "cap_transfer", "in", 1500.0, 1
        )
        # Hostile under cap pressure: friendly neut + nos OUT aimed at the hostile
        # (by other_name) = 3000 + 500 = 3500.
        gf = await _make_gamelog_file(session, CHAR_F1, "neutout")
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_F1, ts=BUCKET_TS_1,
                             effect_type="neut", direction="out", amount=3000.0,
                             other_name="HostileOne", fight_id=fight_id))
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_F1, ts=BUCKET_TS_1,
                             effect_type="nos", direction="out", amount=500.0,
                             other_name="HostileOne", fight_id=fight_id))
        # Tackle (deduped rows): CapMe tackled twice, HostileOne once.
        gf2 = await _make_gamelog_file(session, CHAR_F1, "tackle")
        for tgt in ("CapMe", "CapMe", "HostileOne"):
            session.add(LogEvent(file_id=gf2.file_id, character_id=CHAR_F1, ts=BUCKET_TS_1,
                                 effect_type="scram", direction="out", amount=0.0,
                                 other_name=tgt, fight_id=fight_id,
                                 source_name="NeutBro", target_name=tgt, dedupe_suppressed=False))
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, our_alliance_ids=[_FRIENDLY_ALLI],
                                  settings=get_settings())

    ld = tl.leaders[tl.x.index(int(BUCKET_TS_1.timestamp()))]
    assert ld.top_friendly_cap_pressure is not None
    assert ld.top_friendly_cap_pressure.name == "NeutBro"
    assert ld.top_friendly_cap_pressure.amount == pytest.approx(5000.0)  # neut 4200 + nos 800
    assert ld.top_friendly_cap_recv is not None
    assert ld.top_friendly_cap_recv.name == "CapMe"
    assert ld.top_friendly_cap_recv.amount == pytest.approx(1500.0)
    assert ld.top_hostile_cap_pressure is not None
    assert ld.top_hostile_cap_pressure.name == "HostileOne"
    assert ld.top_hostile_cap_pressure.amount == pytest.approx(3500.0)  # neut 3000 + nos 500
    assert ld.top_friendly_tackle_taken is not None
    assert ld.top_friendly_tackle_taken.name == "CapMe"
    assert ld.top_friendly_tackle_taken.amount == pytest.approx(2.0)
    assert ld.top_hostile_tackle_taken is not None
    assert ld.top_hostile_tackle_taken.name == "HostileOne"
    assert ld.top_hostile_tackle_taken.amount == pytest.approx(1.0)
