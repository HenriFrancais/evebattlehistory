"""Only lines that parsed to an effect are stored as LogEvent rows; the parser's
quality stats are kept on the file so the miss rate is visible."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import GamelogFile, LogEvent
from tests.conftest import CREATOR_HEADERS

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"
HEADER = (
    b"------------------------------------------------------------\n"
    b"  Gamelog\n"
    b"  Listener: Ra'zok Zateki\n"
    b"  Session Started: 2026.01.01 12:00:00\n"
    b"------------------------------------------------------------\n"
)
LOG = HEADER + (
    b"[ 2026.01.01 12:00:01 ] (notify) Some hint that is not combat\n"
    b"[ 2026.01.01 12:00:02 ] (combat) 432 from Enemy Pilot[TST](Brutix) - 250mm Railgun II - Hits\n"
    b"[ 2026.01.01 12:00:03 ] (combat) Your group of guns misses Enemy Pilot completely\n"
    b"[ 2026.01.01 12:00:09 ] (question) Are you sure?\n"
)


async def _ingest(db_session_maker, tmp_path) -> int:  # type: ignore[no-untyped-def]
    from app.config import Settings
    from app.logs.ingest import ingest_log

    settings = Settings(log_dir=tmp_path / "logs")
    async with db_session_maker() as session:
        result = await ingest_log(
            session, settings, "Ra'zok", "20260101_120000_2112615087.txt", LOG, lambda n: None
        )
        await session.commit()
    return result.file_id


async def test_only_effect_lines_become_log_events(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    file_id = await _ingest(db_session_maker, tmp_path)
    async with db_session_maker() as session:
        effects = list((await session.execute(
            select(LogEvent.effect_type).where(LogEvent.file_id == file_id)
        )).scalars())
    assert effects == ["damage"]


async def test_file_keeps_full_time_bounds_and_parser_stats(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    file_id = await _ingest(db_session_maker, tmp_path)
    async with db_session_maker() as session:
        gf = (await session.execute(
            select(GamelogFile).where(GamelogFile.file_id == file_id)
        )).scalar_one()
    assert gf.event_count == 1
    assert gf.combat_lines == 2
    assert gf.unmatched_combat == 1
    # Bounds still span every timestamped line, so fight-window overlap is unchanged.
    assert (gf.log_start_at.second, gf.log_end_at.second) == (1, 9)


async def test_reparse_stores_the_same_shape(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.config import Settings
    from app.logs.reparse import reparse_gamelogs

    file_id = await _ingest(db_session_maker, tmp_path)
    async with db_session_maker() as session:
        await reparse_gamelogs(session, Settings(log_dir=tmp_path / "logs"))
        await session.commit()
        n = (await session.execute(
            select(func.count()).select_from(LogEvent).where(LogEvent.file_id == file_id)
        )).scalar_one()
        gf = (await session.execute(
            select(GamelogFile).where(GamelogFile.file_id == file_id)
        )).scalar_one()
    assert n == 1
    assert (gf.event_count, gf.combat_lines, gf.unmatched_combat) == (1, 2, 1)


async def test_migration_drops_stored_non_effect_rows(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.engine import init_models, reset_engine_for_tests

    db = tmp_path / "old.db"
    monkeypatch.setenv("DB_PATH", str(db))
    get_settings.cache_clear()
    reset_engine_for_tests()
    await init_models(get_settings())
    reset_engine_for_tests()
    with sqlite3.connect(db) as c:
        # Rewind to the v2 shape: the stats columns do not exist yet.
        c.execute("alter table gamelog_file drop column combat_lines")
        c.execute("alter table gamelog_file drop column unmatched_combat")
        c.execute(
            "insert into gamelog_file (file_id, uploaded_by_user, resolved_via, stored_path,"
            " sha256, mime, size, parse_status, event_count, uploaded_at)"
            " values (1,'u','filename','/x','s','text/plain',1,'parsed',2,'2026-01-01')"
        )
        c.execute("insert into log_event (file_id, ts, effect_type, authoritative,"
                  " dedupe_suppressed) values (1,'2026-01-01 12:00:00','damage',0,0)")
        c.execute("insert into log_event (file_id, ts, effect_type, authoritative,"
                  " dedupe_suppressed) values (1,'2026-01-01 12:00:01',NULL,0,0)")
        c.execute("insert into log_event_bucket (fight_id, character_id, bucket_ts,"
                  " effect_type, direction, sum_amount, event_count)"
                  " values (1,1,'2026-01-01 12:00:00','','',0,1)")
        c.execute("pragma user_version = 2")
    await init_models(get_settings())
    reset_engine_for_tests()
    with sqlite3.connect(db) as c:
        assert c.execute("select effect_type from log_event").fetchall() == [("damage",)]
        assert c.execute("select count(*) from log_event_bucket").fetchone()[0] == 0
        assert c.execute("select event_count from gamelog_file").fetchone()[0] == 1


def test_my_logs_reports_unparsed_combat_lines(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = make_client(DB_PATH=str(tmp_path / "t.db"), LOG_DIR=str(tmp_path / "logs"))
    client.post("/api/logs", headers=CREATOR_HEADERS,
                files=[("files", ("20260101_120000_2112615087.txt", LOG, "text/plain"))])
    mine = client.get("/api/logs/mine", headers=CREATOR_HEADERS).json()
    assert mine[0]["combat_lines"] == 2
    assert mine[0]["unmatched_combat"] == 1
