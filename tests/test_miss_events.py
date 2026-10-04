"""Miss events are stored and bucketed, but never enter an existing aggregate;
buckets carry the smallest and largest single hit."""

from __future__ import annotations

import datetime as dt
import sqlite3

from sqlalchemy import select

from app.config import get_settings
from app.db.models import BattleReport, BrFight, LogEventBucket
from tests.test_association import (
    CHAR_A,
    TS_INSIDE,
    _insert_fight,
    _insert_gamelog_file,
    _insert_log_events,
)

BR_ID = "miss-br"


async def _fight_with_hits_and_a_miss(db_session_maker) -> int:  # type: ignore[no-untyped-def]
    """One bucket for CHAR_A: outgoing hits of 100 and 40, plus one outgoing miss."""
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        now = dt.datetime.now(dt.UTC)
        session.add(BattleReport(
            br_id=BR_ID, source="demo", source_url="http://x", source_ref="ref",
            created_by_user="t", status="ready", progress_pct=100, created_at=now,
        ))
        session.add(BrFight(br_id=BR_ID, fight_id=fight_id, seq=0))
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE], direction="out", amount=100.0)
        await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE], direction="out", amount=40.0)
        ids = await _insert_log_events(
            session, file_id, CHAR_A, [TS_INSIDE], effect_type="miss", direction="out", amount=0.0
        )
        assert ids
        await session.commit()
    async with db_session_maker() as session:
        await associate_file(session, file_id)
        await session.commit()
    return fight_id


async def test_bucket_keeps_smallest_and_largest_hit(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    fight_id = await _fight_with_hits_and_a_miss(db_session_maker)
    async with db_session_maker() as session:
        buckets = {
            b.effect_type: b
            for b in (await session.execute(
                select(LogEventBucket).where(LogEventBucket.fight_id == fight_id)
            )).scalars()
        }
    dmg, miss = buckets["damage"], buckets["miss"]
    assert (dmg.sum_amount, dmg.event_count) == (140.0, 2)
    assert (dmg.min_amount, dmg.max_amount) == (40.0, 100.0)
    assert miss.event_count == 1
    assert (miss.min_amount, miss.max_amount) == (None, None)


async def test_miss_excluded_from_aggregates(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_snapshot, fleet_timeline
    from app.analytics.timeline import character_timeline, character_timeline_events

    await _fight_with_hits_and_a_miss(db_session_maker)
    settings = get_settings()
    lo = int(TS_INSIDE.timestamp()) - 60
    hi = lo + 120
    async with db_session_maker() as session:
        fleet = await fleet_timeline(session, BR_ID)
        snap = await fleet_snapshot(session, BR_ID, lo, hi, settings)
        char_tl = await character_timeline(session, BR_ID, CHAR_A)
        events = await character_timeline_events(session, BR_ID, CHAR_A, lo, hi)

    assert [s.key for s in fleet.series] == ["damage:out"]
    assert [v for v in fleet.series[0].values if v] == [140.0]
    assert {c.effect_type for c in snap} == {"damage"}
    assert sum(c.value for c in snap) == 140.0
    assert {s.effect_type for s in char_tl.series} == {"damage"}
    assert {e.effect_type for e in events.events} == {"damage"}


async def test_v4_database_gains_the_v5_columns(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.engine import init_models, reset_engine_for_tests
    from app.db.migrate import LATEST_VERSION

    db = tmp_path / "v4.db"
    monkeypatch.setenv("DB_PATH", str(db))

    async def boot() -> None:
        get_settings.cache_clear()
        reset_engine_for_tests()
        await init_models(get_settings())
        reset_engine_for_tests()

    await boot()
    wanted = [("corporation", "ticker"), ("alliance", "ticker"),
              ("log_event_bucket", "min_amount"), ("log_event_bucket", "max_amount")]
    with sqlite3.connect(db) as c:
        for table, col in wanted:
            c.execute(f"alter table {table} drop column {col}")
        c.execute("pragma user_version = 4")

    await boot()

    assert LATEST_VERSION >= 5
    with sqlite3.connect(db) as c:
        for table, col in wanted:
            assert col in {r[1] for r in c.execute(f"pragma table_info({table})")}
        assert c.execute("pragma user_version").fetchone()[0] == LATEST_VERSION
