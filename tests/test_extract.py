"""Tests for app/logs/extract.py — battle-log slicing, cleaning, assembly."""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.models import BattleReport, BrFight, GamelogFile, LogEvent
from app.logs.extract import build_battle_log, clean_and_slice_gamelog
from tests.test_association import CHAR_A, CHAR_C, FIGHT_START, _insert_fight

# ---------------------------------------------------------------------------
# clean_and_slice_gamelog (pure)
# ---------------------------------------------------------------------------

START = dt.datetime(2026, 6, 10, 20, 0, 0)
END = dt.datetime(2026, 6, 10, 20, 30, 0)

_SAMPLE = """\
------------------------------------------------------------
  Gamelog
  Listener: TestChar Alpha
  Session Started: 2026.06.10 19:00:00
------------------------------------------------------------
[ 2026.06.10 18:00:00 ] (combat) <b>way before</b>
[ 2026.06.10 20:00:00 ] (combat) <b>at start boundary</b>
[ 2026.06.10 20:15:00 ] (combat) <color=0xff0><b>432</b></color> <font>from</font> <b>FakeEnemy</b>
[ 2026.06.10 20:30:00 ] (combat) <b>at end boundary</b>
[ 2026.06.10 20:45:00 ] (combat) <b>way after</b>
"""


def test_header_block_dropped() -> None:
    out = clean_and_slice_gamelog(_SAMPLE, START, END)
    assert "Gamelog" not in out
    assert "Listener" not in out
    assert "Session Started" not in out


def test_keeps_in_window_drops_out_of_window() -> None:
    out = clean_and_slice_gamelog(_SAMPLE, START, END)
    assert "432" in out
    assert "way before" not in out
    assert "way after" not in out


def test_window_boundaries_inclusive() -> None:
    out = clean_and_slice_gamelog(_SAMPLE, START, END)
    assert "at start boundary" in out
    assert "at end boundary" in out


def test_markup_stripped_but_envelope_kept() -> None:
    out = clean_and_slice_gamelog(_SAMPLE, START, END)
    assert "<b>" not in out
    assert "<color" not in out
    assert "<font" not in out
    # Canonical envelope preserved, body cleaned.
    assert "[ 2026.06.10 20:15:00 ] (combat) 432 from FakeEnemy" in out


def test_empty_when_nothing_in_window() -> None:
    far = dt.datetime(2030, 1, 1, 0, 0, 0)
    assert clean_and_slice_gamelog(_SAMPLE, far, far) == ""


def test_continuation_line_carried_under_prior_timestamp() -> None:
    text = (
        "[ 2026.06.10 20:15:00 ] (combat) <b>start</b>\n"
        "continued <b>detail</b> line\n"
        "[ 2026.06.10 20:45:00 ] (combat) <b>after</b>\n"
        "trailing out-of-window continuation\n"
    )
    out = clean_and_slice_gamelog(text, START, END)
    assert "continued detail line" in out  # carried under the in-window 20:15 line
    assert "trailing out-of-window continuation" not in out  # carried under 20:45 (out)


def test_accepts_tz_aware_window() -> None:
    out = clean_and_slice_gamelog(
        _SAMPLE,
        START.replace(tzinfo=dt.UTC),
        END.replace(tzinfo=dt.UTC),
    )
    assert "432" in out


# ---------------------------------------------------------------------------
# build_battle_log (DB + disk)
# ---------------------------------------------------------------------------


def _gamelog_text(in_window_line: str) -> str:
    return (
        "------------------------------------------------------------\n"
        "  Gamelog\n"
        "  Listener: CharA\n"
        "------------------------------------------------------------\n"
        "[ 2026.06.10 18:00:00 ] (combat) <b>before window</b>\n"
        f"{in_window_line}\n"
        "[ 2026.06.10 21:00:00 ] (combat) <b>after window</b>\n"
    )


async def _add_br_fight(session, fight_id: int) -> str:  # type: ignore[no-untyped-def]
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
    return br_id


async def _add_file_with_events(  # type: ignore[no-untyped-def]
    session,
    tmp_path: Path,
    character_id: int,
    fight_id: int,
    text: str,
    log_start: dt.datetime,
    filename: str,
) -> int:
    path = tmp_path / f"{uuid.uuid4().hex}.txt"
    path.write_text(text, encoding="utf-8")
    gf = GamelogFile(
        uploaded_by_user="UserAlpha",
        claimed_character_id=character_id,
        character_name=f"Char{character_id}",
        original_filename=filename,
        resolved_via="filename",
        log_start_at=log_start,
        log_end_at=log_start + dt.timedelta(hours=2),
        stored_path=str(path),
        sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        mime="text/plain",
        size=len(text),
        parse_status="parsed",
        event_count=1,
        uploaded_at=dt.datetime.now(dt.UTC),
    )
    session.add(gf)
    await session.flush()
    session.add(
        LogEvent(
            file_id=gf.file_id,
            character_id=character_id,
            ts=FIGHT_START + dt.timedelta(minutes=15),
            direction="in",
            effect_type="damage",
            amount=100.0,
            fight_id=fight_id,
        )
    )
    await session.flush()
    return gf.file_id


async def test_build_single_file(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = await _add_br_fight(session, fight_id)
        await _add_file_with_events(
            session, tmp_path, CHAR_A, fight_id,
            _gamelog_text("[ 2026.06.10 20:15:00 ] (combat) <b>432</b> from <b>FakeEnemy</b>"),
            FIGHT_START, "gamelog_a.txt",
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await build_battle_log(session, br_id, CHAR_A)

    assert result is not None
    text, filename = result
    assert "432 from FakeEnemy" in text
    assert "before window" not in text
    assert "after window" not in text
    assert "Gamelog" not in text
    assert "<b>" not in text
    assert filename.endswith(".txt")
    assert br_id in filename


async def test_build_multi_file_concatenates_in_order(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = await _add_br_fight(session, fight_id)
        await _add_file_with_events(
            session, tmp_path, CHAR_A, fight_id,
            _gamelog_text("[ 2026.06.10 20:10:00 ] (combat) <b>first file line</b>"),
            FIGHT_START, "early.txt",
        )
        await _add_file_with_events(
            session, tmp_path, CHAR_A, fight_id,
            _gamelog_text("[ 2026.06.10 20:20:00 ] (combat) <b>second file line</b>"),
            FIGHT_START + dt.timedelta(minutes=30), "late.txt",
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await build_battle_log(session, br_id, CHAR_A)

    assert result is not None
    text, _ = result
    assert "=== file: early.txt" in text
    assert "=== file: late.txt" in text
    assert text.index("early.txt") < text.index("late.txt")
    assert "first file line" in text
    assert "second file line" in text


async def test_build_no_logs_returns_none(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = await _add_br_fight(session, fight_id)
        await _add_file_with_events(
            session, tmp_path, CHAR_A, fight_id,
            _gamelog_text("[ 2026.06.10 20:15:00 ] (combat) <b>x</b>"),
            FIGHT_START, "a.txt",
        )
        await session.commit()

    async with db_session_maker() as session:
        # CHAR_C has no log events in this battle.
        result = await build_battle_log(session, br_id, CHAR_C)

    assert result is None


async def test_build_unreadable_file_skipped(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = await _add_br_fight(session, fight_id)
        file_id = await _add_file_with_events(
            session, tmp_path, CHAR_A, fight_id,
            _gamelog_text("[ 2026.06.10 20:15:00 ] (combat) <b>x</b>"),
            FIGHT_START, "gone.txt",
        )
        # Point the stored file at a path that doesn't exist.
        gf = (
            await session.execute(select(GamelogFile).where(GamelogFile.file_id == file_id))
        ).scalar_one()
        gf.stored_path = str(tmp_path / "does-not-exist.txt")
        await session.commit()

    async with db_session_maker() as session:
        result = await build_battle_log(session, br_id, CHAR_A)

    assert result is None  # only file unreadable → nothing to serve


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


async def _boot_app_with_data(tmp_path, monkeypatch, *, with_file: bool):  # type: ignore[no-untyped-def]
    """Init a temp DB, insert a BR+fight (+ optional log file), return (app, br_id)."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app
    from tests.conftest import TEST_TOKEN

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    async with get_sessionmaker(settings)() as session:
        fight_id = await _insert_fight(session)
        br_id = await _add_br_fight(session, fight_id)
        if with_file:
            await _add_file_with_events(
                session, tmp_path, CHAR_A, fight_id,
                _gamelog_text("[ 2026.06.10 20:15:00 ] (combat) <b>432</b> from <b>FakeEnemy</b>"),
                FIGHT_START, "gamelog_a.txt",
            )
        await session.commit()

    get_app_config.cache_clear()
    return create_app(), br_id


async def test_api_download_returns_attachment(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from tests.conftest import CREATOR_HEADERS

    app, br_id = await _boot_app_with_data(tmp_path, monkeypatch, with_file=True)
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/logs/{CHAR_A}/download", headers=CREATOR_HEADERS)

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert ".txt" in resp.headers["content-disposition"]
    assert "432 from FakeEnemy" in resp.text
    assert "<b>" not in resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


async def test_api_download_403_for_other_users_character(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from tests.conftest import MEMBER_HEADERS

    app, br_id = await _boot_app_with_data(tmp_path, monkeypatch, with_file=True)
    with TestClient(app) as client:
        # A non-elevated member who does not own CHAR_A.
        resp = client.get(f"/api/brs/{br_id}/logs/{CHAR_A}/download", headers=MEMBER_HEADERS)

    assert resp.status_code == 403

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


async def test_api_download_404_when_no_logs(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from tests.conftest import CREATOR_HEADERS

    app, br_id = await _boot_app_with_data(tmp_path, monkeypatch, with_file=False)
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/logs/{CHAR_A}/download", headers=CREATOR_HEADERS)

    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


async def test_build_no_fights_returns_none(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id, source="demo", source_url="http://x", source_ref="ref",
                created_by_user="test", status="ready", progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    async with db_session_maker() as session:
        result = await build_battle_log(session, br_id, CHAR_A)

    assert result is None
