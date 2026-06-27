"""TDD tests for Task 3.1: per-character timeline analytics + API endpoints.

Scenario
--------
* Character CHAR_A (2100000001) participates in fight_1.
* fight_1: started_at=2026-06-10 20:00 UTC, ended_at=2026-06-10 20:30 UTC.
* Buckets are inserted directly (bypassing the full association pipeline
  for speed) so we test the analytics logic in isolation.

Tests:
1.  character_timeline returns aligned x + series.
2.  A known bucket's sum lands in the right series at the right x.
3.  fights list is present with correct shape.
4.  Empty series (no error) for a character with no logs in the BR.
5.  Alignment: a series missing a bucket at some x has None there
    (len(values) == len(x) for every series).
6.  "" effect_type/direction surfaced as "unknown"/null in output labels.
7.  character_timeline_events: raw events ordered by ts.
8.  character_timeline_events: effect_type/direction filter narrows results.
9.  character_timeline_events: cap + truncated flag.
10. API: timeline endpoint returns expected shape.
11. API: events endpoint returns expected shape.
12. API: 404 unknown BR (both endpoints).
13. API: events endpoint from > to → 400.
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
    LogEventBucket,
)

# Re-use helpers from Phase-2 test module
from tests.test_association import (
    _insert_fight,
    _insert_gamelog_file,
)

CHAR_A = 2100000001
CHAR_B = 2200000001
CHAR_X = 9000000001  # has no logs in the BR

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC)

# Two distinct bucket timestamps (epoch-aligned to 5s)
# 2026-06-10 20:00:00 UTC = epoch 1749513600
BUCKET_TS_1 = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
# 2026-06-10 20:00:05 UTC = epoch 1749513605
BUCKET_TS_2 = dt.datetime(2026, 6, 10, 20, 0, 5, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_br_with_fight(session):  # type: ignore[no-untyped-def]
    """Insert a Fight + BattleReport + BrFight.  Returns (br_id, fight_id)."""
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


async def _insert_bucket(  # type: ignore[no-untyped-def]
    session,
    fight_id: int,
    character_id: int,
    bucket_ts: dt.datetime,
    effect_type: str = "damage",
    direction: str = "in",
    sum_amount: float = 500.0,
    event_count: int = 5,
) -> None:
    """Insert a single LogEventBucket row (direct, no association pipeline)."""
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
# 1-6: analytics layer
# ---------------------------------------------------------------------------


async def test_character_timeline_returns_aligned_x_and_series(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """character_timeline returns sorted x and one series per (effect_type, direction)."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 100.0, 2)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "damage", "in", 200.0, 3)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep", "out", 50.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_A)

    # x must be sorted and unique
    assert tl.x == sorted(tl.x)
    assert len(tl.x) == len(set(tl.x))
    # All values arrays same length as x
    for s in tl.series:
        assert len(s.values) == len(tl.x), f"series {s.key} length mismatch"


async def test_character_timeline_bucket_sum_in_correct_series_and_x(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A known bucket's sum_amount lands in the right series at the matching x index."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 999.0, 3)
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_A)

    expected_x = int(BUCKET_TS_1.timestamp())
    assert expected_x in tl.x
    idx = tl.x.index(expected_x)

    damage_in = next(s for s in tl.series if s.key == "damage:in")
    assert damage_in.values[idx] == pytest.approx(999.0)


async def test_character_timeline_fights_list_present(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """character_timeline includes a fights list with fight metadata."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_A)

    assert len(tl.fights) == 1
    f = tl.fights[0]
    assert f.fight_id == fight_id
    assert f.seq == 0
    assert f.started_at is not None
    assert f.ended_at is not None
    assert f.system_id is not None


async def test_character_timeline_empty_for_char_with_no_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """character_timeline returns empty series (no error) for character with no buckets."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # Insert buckets for CHAR_A only — CHAR_X has none
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_X)

    assert tl.x == []
    assert tl.series == []
    # fights list still present (fight metadata is BR-level, not char-level)
    assert len(tl.fights) == 1  # BR has one fight even though CHAR_X has no logs


async def test_character_timeline_alignment_none_gaps(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Series missing a bucket at some x position has None there (not 0)."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # "rep:out" only at BUCKET_TS_1; "damage:in" at both
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 10.0)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_2, "damage", "in", 20.0)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep", "out", 5.0)
        # No rep:out at BUCKET_TS_2 → that position should be None
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_A)

    assert len(tl.x) == 2
    ts2_idx = tl.x.index(int(BUCKET_TS_2.timestamp()))
    rep_out = next(s for s in tl.series if s.key == "rep:out")
    assert rep_out.values[ts2_idx] is None


async def test_character_timeline_unknown_effect_type_direction(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Buckets with effect_type='' and direction='' use 'unknown'/null in output."""
    from app.analytics.timeline import character_timeline

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # "" means unknown — as stored by the association pipeline
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "", "", 77.0)
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, CHAR_A)

    assert len(tl.series) == 1
    s = tl.series[0]
    # key convention: "unknown:unknown"
    assert s.key == "unknown:unknown"
    # effect_type and direction are None (null) in the output
    assert s.effect_type is None
    assert s.direction is None


# ---------------------------------------------------------------------------
# 7-9: drill-down events
# ---------------------------------------------------------------------------


async def test_character_timeline_events_normalizes_empty_strings(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Events with "" effect_type/direction are returned as None (not "")."""
    from app.analytics.timeline import character_timeline_events

    ts1 = FIGHT_START + dt.timedelta(seconds=5)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        session.add(LogEvent(
            file_id=file_id, character_id=CHAR_A, ts=ts1,
            direction="", effect_type="", amount=7.0, fight_id=fight_id,
        ))
        await session.commit()

    async with db_session_maker() as session:
        result = await character_timeline_events(
            session, br_id, CHAR_A,
            t_from=int(FIGHT_START.timestamp()),
            t_to=int(FIGHT_END.timestamp()),
        )

    assert len(result.events) == 1
    e = result.events[0]
    assert e.effect_type is None, f"expected None, got {e.effect_type!r}"
    assert e.direction is None, f"expected None, got {e.direction!r}"


async def test_character_timeline_events_ordered_by_ts(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """character_timeline_events returns events in ascending ts order."""
    from app.analytics.timeline import character_timeline_events

    ts1 = FIGHT_START + dt.timedelta(seconds=10)
    ts2 = FIGHT_START + dt.timedelta(seconds=20)
    ts3 = FIGHT_START + dt.timedelta(seconds=30)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        # Insert out of order
        for ts in [ts3, ts1, ts2]:
            ev = LogEvent(
                file_id=file_id,
                character_id=CHAR_A,
                ts=ts,
                direction="in",
                effect_type="damage",
                amount=10.0,
                fight_id=fight_id,
            )
            session.add(ev)
        await session.commit()

    async with db_session_maker() as session:
        result = await character_timeline_events(
            session, br_id, CHAR_A,
            t_from=int(FIGHT_START.timestamp()),
            t_to=int(FIGHT_END.timestamp()),
        )

    assert not result.truncated
    assert len(result.events) == 3
    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)


async def test_character_timeline_events_filter_by_effect_type_direction(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """effect_type/direction filter narrows results."""
    from app.analytics.timeline import character_timeline_events

    ts1 = FIGHT_START + dt.timedelta(seconds=5)
    ts2 = FIGHT_START + dt.timedelta(seconds=10)

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        session.add(LogEvent(
            file_id=file_id, character_id=CHAR_A, ts=ts1,
            direction="in", effect_type="damage", amount=50.0, fight_id=fight_id,
        ))
        session.add(LogEvent(
            file_id=file_id, character_id=CHAR_A, ts=ts2,
            direction="out", effect_type="rep", amount=30.0, fight_id=fight_id,
        ))
        await session.commit()

    async with db_session_maker() as session:
        result = await character_timeline_events(
            session, br_id, CHAR_A,
            t_from=int(FIGHT_START.timestamp()),
            t_to=int(FIGHT_END.timestamp()),
            effect_type="damage",
            direction="in",
        )

    assert len(result.events) == 1
    assert result.events[0].effect_type == "damage"
    assert result.events[0].direction == "in"


async def test_character_timeline_events_cap_and_truncated_flag(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Events are capped at EVENTS_CAP and truncated flag is set."""
    from app.analytics.timeline import EVENTS_CAP, character_timeline_events

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        # Insert EVENTS_CAP + 5 events
        for i in range(EVENTS_CAP + 5):
            ts = FIGHT_START + dt.timedelta(seconds=i)
            session.add(LogEvent(
                file_id=file_id, character_id=CHAR_A, ts=ts,
                direction="in", effect_type="damage", amount=1.0, fight_id=fight_id,
            ))
        await session.commit()

    async with db_session_maker() as session:
        result = await character_timeline_events(
            session, br_id, CHAR_A,
            t_from=int(FIGHT_START.timestamp()),
            t_to=int((FIGHT_START + dt.timedelta(seconds=EVENTS_CAP + 10)).timestamp()),
        )

    assert result.truncated is True
    assert len(result.events) == EVENTS_CAP


# ---------------------------------------------------------------------------
# 10-13: API layer
# ---------------------------------------------------------------------------


from tests.conftest import CREATOR_HEADERS, TEST_TOKEN  # noqa: E402


@pytest.mark.asyncio
async def test_api_timeline_returns_expected_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/characters/{char_id}/timeline returns CharacterTimeline shape."""
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
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 200.0)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = CREATOR_HEADERS
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/characters/{CHAR_A}/timeline", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert "x" in data
    assert "series" in data
    assert "fights" in data
    assert isinstance(data["x"], list)
    assert isinstance(data["series"], list)
    assert len(data["series"]) == 1
    s = data["series"][0]
    assert s["key"] == "damage:in"
    assert len(s["values"]) == len(data["x"])

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_events_returns_expected_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/characters/{char_id}/events returns TimelineEventList shape."""
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
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        ts1 = FIGHT_START + dt.timedelta(seconds=5)
        session.add(LogEvent(
            file_id=file_id, character_id=CHAR_A, ts=ts1,
            direction="in", effect_type="damage", amount=42.0, fight_id=fight_id,
        ))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = CREATOR_HEADERS
    t_from = int(FIGHT_START.timestamp())
    t_to = int(FIGHT_END.timestamp())
    with TestClient(app) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{CHAR_A}/events?from={t_from}&to={t_to}",
            headers=hdrs,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "truncated" in data
    assert len(data["events"]) == 1
    e = data["events"][0]
    assert e["effect_type"] == "damage"
    assert e["amount"] == pytest.approx(42.0)

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_timeline_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET timeline for unknown BR returns 404."""
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
    hdrs = CREATOR_HEADERS
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/no-such-br/characters/{CHAR_A}/timeline", headers=hdrs)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_events_404_unknown_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET events for unknown BR returns 404."""
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
    hdrs = CREATOR_HEADERS
    t_from = int(FIGHT_START.timestamp())
    t_to = int(FIGHT_END.timestamp())
    with TestClient(app) as client:
        resp = client.get(
            f"/api/brs/no-such-br/characters/{CHAR_A}/events?from={t_from}&to={t_to}",
            headers=hdrs,
        )

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_events_400_from_greater_than_to(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET events with from > to returns 400."""
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
        br_id, _ = await _make_br_with_fight(session)
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = CREATOR_HEADERS
    # from > to is invalid
    t_from = int(FIGHT_END.timestamp())
    t_to = int(FIGHT_START.timestamp())
    with TestClient(app) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{CHAR_A}/events?from={t_from}&to={t_to}",
            headers=hdrs,
        )

    assert resp.status_code == 400

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# Cross-log reconstruction: a character's remote reps / cap are reconstructed
# from OTHER friendly logs that name them (deduped against their own log), so a
# logi whose own log is missing/incomplete still gets a timeline.
# ---------------------------------------------------------------------------

_LOGI = 2300000001


async def _logfile(session, char_id, sha):  # type: ignore[no-untyped-def]
    from app.db.models import GamelogFile
    gf = GamelogFile(uploaded_by_user="u", claimed_character_id=char_id, resolved_via="filename",
                     stored_path=f"/{sha}", sha256=sha, mime="text/plain", size=1,
                     parse_status="parsed", event_count=1, uploaded_at=dt.datetime.now(dt.UTC))
    session.add(gf)
    await session.flush()
    return gf


async def test_character_timeline_reconstructs_reps_from_other_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A logi with NO usable own log still shows outgoing reps, reconstructed from
    the recipients' logs that recorded 'repaired by <logi>'."""
    from app.analytics.timeline import character_timeline
    from app.db.models import Character

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(Character(character_id=_LOGI, name="Logi Covin",
                                      last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        # Recipient CHAR_A logs reps received from the logi, in CHAR_A's own log.
        gf = await _logfile(session, CHAR_A, "recip")
        for amt in (600.0, 400.0):
            session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=BUCKET_TS_1,
                                 direction="in", effect_type="rep_armor", amount=amt,
                                 other_name="Logi Covin", fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, _LOGI)

    s = next((s for s in tl.series if s.effect_type == "rep_armor" and s.direction == "out"), None)
    assert s is not None, "logi's outgoing reps should be reconstructed from recipients' logs"
    assert sum(v for v in s.values if v is not None) == pytest.approx(1000.0)


async def test_character_timeline_reps_not_double_counted_across_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """When the logi AND the recipient both logged the same rep tick, it counts once."""
    from app.analytics.timeline import character_timeline
    from app.db.models import Character

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(Character(character_id=_LOGI, name="Logi Covin",
                                      last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        recip_name = f"Char{CHAR_A}"  # _insert_fight names characters "Char<id>"
        # Logi's own log: outgoing rep to CHAR_A.
        gf_logi = await _logfile(session, _LOGI, "logi")
        session.add(LogEvent(file_id=gf_logi.file_id, character_id=_LOGI, ts=BUCKET_TS_1,
                             direction="out", effect_type="rep_armor", amount=1000.0,
                             other_name=recip_name, fight_id=fight_id))
        # Recipient's own log: the SAME physical tick, incoming from the logi.
        gf_recip = await _logfile(session, CHAR_A, "recip")
        session.add(LogEvent(file_id=gf_recip.file_id, character_id=CHAR_A, ts=BUCKET_TS_1,
                             direction="in", effect_type="rep_armor", amount=1000.0,
                             other_name="Logi Covin", fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        tl = await character_timeline(session, br_id, _LOGI)

    s = next((s for s in tl.series if s.effect_type == "rep_armor" and s.direction == "out"), None)
    assert s is not None
    assert sum(v for v in s.values if v is not None) == pytest.approx(1000.0)  # once, not 2000
