"""TDD tests for Task 2.2: log upload, dedupe, persist, character resolution."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"

GAMELOG_HEADER = (
    b"------------------------------------------------------------\n"
    b"  Gamelog\n"
    b"  Listener: TestChar Alpha\n"
    b"  Session Started: 2026.06.16 19:21:14\n"
    b"------------------------------------------------------------\n"
)


def _settings_with_tmp(tmp_path: Path) -> Settings:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        db_path=tmp_path / "test.db",
        log_dir=log_dir,
        max_log_mb=20,
        data_source="demo",
        nv_token="test-token",
    )


def _make_roster_lookup(name_to_id: dict[str, int]):  # type: ignore[type-arg]
    """Build a roster_lookup callable from a name→id dict (lowercase keys)."""

    def lookup(name: str) -> int | None:
        return name_to_id.get(name.lower())

    return lookup


# ---------------------------------------------------------------------------
# Task 1: DB model existence
# ---------------------------------------------------------------------------


async def test_gamelog_file_table_exists(db_session_maker):  # type: ignore[no-untyped-def]
    """GamelogFile and LogEvent tables must exist after init_models."""
    async with db_session_maker() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result.fetchall()}
    assert "gamelog_file" in tables
    assert "log_event" in tables


# ---------------------------------------------------------------------------
# Task 2: Storage validation
# ---------------------------------------------------------------------------


def test_validate_and_store_writes_file(tmp_path: Path) -> None:
    from app.logs.store import StoreResult, validate_and_store

    settings = _settings_with_tmp(tmp_path)
    raw = GAMELOG_HEADER + b"[ 2026.06.16 19:21:15 ] (hint) some line\n"
    result = validate_and_store(raw, settings)
    assert isinstance(result, StoreResult)
    assert result.mime == "text/plain"
    expected_sha = hashlib.sha256(raw).hexdigest()
    assert result.sha256 == expected_sha
    assert result.stored_path.exists()
    assert result.stored_path.read_bytes() == raw


def test_validate_and_store_oversize_rejected(tmp_path: Path) -> None:
    from app.logs.store import validate_and_store

    tiny_settings = Settings(
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        max_log_mb=0,  # 0 MB → any content too big
        data_source="demo",
        nv_token="test-token",
    )
    with pytest.raises(ValueError, match="too large"):
        validate_and_store(GAMELOG_HEADER, tiny_settings)


def test_validate_and_store_non_gamelog_rejected(tmp_path: Path) -> None:
    from app.logs.store import validate_and_store

    settings = _settings_with_tmp(tmp_path)
    with pytest.raises(ValueError, match="not a valid gamelog"):
        validate_and_store(b"This is not a gamelog file at all", settings)


def test_validate_and_store_idempotent_no_double_write(tmp_path: Path) -> None:
    """Writing the same bytes twice must not raise; the second call is a no-op on disk."""
    from app.logs.store import validate_and_store

    settings = _settings_with_tmp(tmp_path)
    raw = GAMELOG_HEADER + b"line\n"
    r1 = validate_and_store(raw, settings)
    r2 = validate_and_store(raw, settings)
    assert r1.sha256 == r2.sha256
    # Only one file on disk
    files = list(settings.log_dir.glob("*.txt"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# Task 3: Ingest + dedupe + character resolution
# ---------------------------------------------------------------------------


async def test_ingest_log_parses_and_persists(  # type: ignore[no-untyped-def]
    tmp_path: Path, db_session_maker
) -> None:
    """Ingest a real fixture log; verify GamelogFile row + N LogEvent rows created."""
    from app.db.models import GamelogFile, LogEvent
    from app.logs.ingest import GamelogFileResult, ingest_log

    settings = _settings_with_tmp(tmp_path)
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    filename = "20260616_192114_2112615087.txt"

    async with db_session_maker() as session:
        result = await ingest_log(
            session,
            settings,
            uploaded_by_user="Ra'zok",
            filename=filename,
            raw_bytes=raw,
            roster_lookup=_make_roster_lookup({"testchar alpha": 2112615087}),
        )
        await session.commit()

    assert isinstance(result, GamelogFileResult)
    assert result.duplicate is False
    assert result.parse_status == "parsed"
    assert result.event_count > 0
    assert result.character_id == 2112615087  # resolved via filename
    assert result.file_id is not None

    # Verify DB rows
    async with db_session_maker() as session:
        gf = (
            await session.execute(
                select(GamelogFile).where(GamelogFile.file_id == result.file_id)
            )
        ).scalar_one()
        event_count_db = (
            await session.execute(
                select(func.count()).select_from(LogEvent).where(
                    LogEvent.file_id == result.file_id
                )
            )
        ).scalar()

    assert gf.uploaded_by_user == "Ra'zok"
    assert gf.claimed_character_id == 2112615087
    assert gf.resolved_via == "filename"
    assert gf.parse_status == "parsed"
    assert gf.event_count == result.event_count
    assert event_count_db == result.event_count


async def test_ingest_log_dedupe(  # type: ignore[no-untyped-def]
    tmp_path: Path, db_session_maker
) -> None:
    """Re-uploading the same bytes must return duplicate=True with NO new rows."""
    from app.db.models import GamelogFile, LogEvent
    from app.logs.ingest import ingest_log

    settings = _settings_with_tmp(tmp_path)
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    filename = "20260616_192114_2112615087.txt"
    roster_lookup = _make_roster_lookup({"testchar alpha": 2112615087})

    async with db_session_maker() as session:
        r1 = await ingest_log(session, settings, "Ra'zok", filename, raw, roster_lookup)
        await session.commit()

    async with db_session_maker() as session:
        r2 = await ingest_log(session, settings, "Ra'zok", filename, raw, roster_lookup)
        await session.commit()

    # Dedupe: second call is a no-op
    assert r2.duplicate is True
    assert r2.file_id == r1.file_id

    # Counts unchanged
    async with db_session_maker() as session:
        gf_count = (
            await session.execute(select(func.count()).select_from(GamelogFile))
        ).scalar()
        ev_count = (
            await session.execute(select(func.count()).select_from(LogEvent))
        ).scalar()

    assert gf_count == 1  # Only one GamelogFile row
    assert ev_count == r1.event_count  # No duplicate events


async def test_ingest_log_unresolved(  # type: ignore[no-untyped-def]
    tmp_path: Path, db_session_maker
) -> None:
    """A log with no charId and unknown listener name: stored, unresolved, events persisted."""
    from app.db.models import GamelogFile, LogEvent
    from app.logs.ingest import ingest_log

    settings = _settings_with_tmp(tmp_path)
    raw = (FIXTURES / "no_char_id.txt").read_bytes()
    filename = "20231006_204512.txt"  # no char_id in filename

    async with db_session_maker() as session:
        result = await ingest_log(
            session,
            settings,
            uploaded_by_user="LineMember",
            filename=filename,
            raw_bytes=raw,
            roster_lookup=_make_roster_lookup({}),  # empty roster → unresolved
        )
        await session.commit()

    assert result.duplicate is False
    assert result.parse_status == "unresolved"
    assert result.character_id is None
    assert result.character_name is None  # listener in fixture is unknown in roster
    assert result.original_filename == "20231006_204512.txt"

    async with db_session_maker() as session:
        gf = (
            await session.execute(
                select(GamelogFile).where(GamelogFile.file_id == result.file_id)
            )
        ).scalar_one()
        ev_count = (
            await session.execute(
                select(func.count()).select_from(LogEvent).where(
                    LogEvent.file_id == result.file_id
                )
            )
        ).scalar()

    assert gf.parse_status == "unresolved"
    assert ev_count == result.event_count  # Events still persisted


async def test_ingest_log_error_on_bad_content(  # type: ignore[no-untyped-def]
    tmp_path: Path, db_session_maker
) -> None:
    """Non-gamelog content raises ValueError (caught at API layer) — no rows persisted."""
    from app.db.models import GamelogFile, LogEvent
    from app.logs.ingest import ingest_log

    settings = _settings_with_tmp(tmp_path)
    with pytest.raises(ValueError):
        async with db_session_maker() as session:
            await ingest_log(
                session,
                settings,
                uploaded_by_user="Ra'zok",
                filename="corrupt.txt",
                raw_bytes=b"not a gamelog",
                roster_lookup=_make_roster_lookup({}),
            )

    # No partial persist
    async with db_session_maker() as session:
        gf_count = (await session.execute(select(func.count()).select_from(GamelogFile))).scalar()
        ev_count = (await session.execute(select(func.count()).select_from(LogEvent))).scalar()
    assert gf_count == 0
    assert ev_count == 0


async def test_ingest_log_concurrent_duplicate(  # type: ignore[no-untyped-def]
    tmp_path: Path, db_session_maker
) -> None:
    """Simulate race: insert same sha256 directly between check and flush.

    Verifies that duplicate=True is returned without error.
    """
    import datetime as dt
    import hashlib

    from app.db.models import GamelogFile
    from app.logs.ingest import ingest_log

    settings = _settings_with_tmp(tmp_path)
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    filename = "20260616_192114_2112615087.txt"
    roster_lookup = _make_roster_lookup({"testchar alpha": 2112615087})

    # Insert the "winner" row first (simulates the other concurrent request committing first)
    sha = hashlib.sha256(raw).hexdigest()
    async with db_session_maker() as session:
        winner = GamelogFile(
            uploaded_by_user="other_user",
            claimed_character_id=2112615087,
            listener_name="TestChar Alpha",
            character_name="TestChar Alpha",
            original_filename=filename,
            resolved_via="filename",
            session_started_at=None,
            log_start_at=None,
            log_end_at=None,
            stored_path="/fake/path.txt",
            sha256=sha,
            mime="text/plain",
            size=len(raw),
            parse_status="parsed",
            event_count=0,
            uploaded_at=dt.datetime.now(dt.UTC),
        )
        session.add(winner)
        await session.commit()
        await session.refresh(winner)
        winner_id = winner.file_id

    # ingest_log will find the existing row via SELECT and return duplicate=True
    async with db_session_maker() as session:
        result = await ingest_log(session, settings, "Ra'zok", filename, raw, roster_lookup)
        await session.commit()

    assert result.duplicate is True
    assert result.file_id == winner_id

    # Verify only 1 GamelogFile row exists
    async with db_session_maker() as session:
        count = (await session.execute(select(func.count()).select_from(GamelogFile))).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Task 4: Upload API
# ---------------------------------------------------------------------------

# Import conftest constants so these tests can reference them
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS  # noqa: E402


def test_post_logs_requires_auth(client) -> None:  # type: ignore[no-untyped-def]
    """Unauthenticated upload → 401."""
    response = client.post("/api/logs", files=[])
    assert response.status_code == 401


def test_post_logs_single_file_parsed(make_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Upload one valid file → response list with status parsed or unresolved."""
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20"
    )
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    response = client.post(
        "/api/logs",
        files=[("files", ("20260616_192114_2112615087.txt", raw, "text/plain"))],
        headers=CREATOR_HEADERS,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    r = results[0]
    assert r["status"] in ("parsed", "unresolved")  # demo roster may be empty
    assert r["event_count"] > 0
    assert r["filename"] == "20260616_192114_2112615087.txt"
    assert "character_name" in r  # may be None or a string


def test_post_logs_dedupe_in_batch(make_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Uploading the same file twice in one batch: second entry is duplicate."""
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20"
    )
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    response = client.post(
        "/api/logs",
        files=[
            ("files", ("file_a.txt", raw, "text/plain")),
            ("files", ("file_b.txt", raw, "text/plain")),  # same content
        ],
        headers=CREATOR_HEADERS,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    statuses = {r["status"] for r in results}
    assert "duplicate" in statuses


def test_post_logs_corrupt_file_error_no_abort(  # type: ignore[no-untyped-def]
    make_client, tmp_path: Path
) -> None:
    """A corrupt file in a batch does not abort; returns error status for that file."""
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20"
    )
    good = (FIXTURES / "full_fight.txt").read_bytes()
    bad = b"this is not a gamelog at all"
    response = client.post(
        "/api/logs",
        files=[
            ("files", ("good.txt", good, "text/plain")),
            ("files", ("bad.txt", bad, "text/plain")),
        ],
        headers=CREATOR_HEADERS,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    statuses = {r["filename"]: r["status"] for r in results}
    assert statuses["good.txt"] in ("parsed", "unresolved")
    assert statuses["bad.txt"] == "error"


def test_get_logs_mine_returns_only_caller_files(  # type: ignore[no-untyped-def]
    make_client, tmp_path: Path
) -> None:
    """GET /api/logs/mine scopes by user: each user sees only their own uploads."""
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20"
    )

    # Upload as Ra'zok
    raw_a = (FIXTURES / "full_fight.txt").read_bytes()
    client.post(
        "/api/logs",
        files=[("files", ("log_a.txt", raw_a, "text/plain"))],
        headers=CREATOR_HEADERS,
    )

    # Upload as LineMember using a different fixture
    raw_b = (FIXTURES / "damage_in.txt").read_bytes()
    client.post(
        "/api/logs",
        files=[("files", ("log_b.txt", raw_b, "text/plain"))],
        headers=MEMBER_HEADERS,
    )

    # Ra'zok sees only his file
    resp_razok = client.get("/api/logs/mine", headers=CREATOR_HEADERS)
    assert resp_razok.status_code == 200
    razok_files = resp_razok.json()
    assert len(razok_files) >= 1
    assert all(f["uploaded_at"] is not None for f in razok_files)

    # LineMember sees only her file
    resp_member = client.get("/api/logs/mine", headers=MEMBER_HEADERS)
    assert resp_member.status_code == 200
    member_files = resp_member.json()
    assert len(member_files) >= 1

    # Ensure no overlap in file_ids
    razok_ids = {f["file_id"] for f in razok_files}
    member_ids = {f["file_id"] for f in member_files}
    assert razok_ids.isdisjoint(member_ids)

    # Each file entry must carry filename and character_name keys (values may be None)
    for f in razok_files:
        assert "filename" in f
        assert "character_name" in f
    for f in member_files:
        assert "filename" in f
        assert "character_name" in f


def test_post_logs_oversize_rejected_per_file(  # type: ignore[no-untyped-def]
    make_client, tmp_path: Path
) -> None:
    """Files exceeding max_log_mb are reported as error (not batch abort)."""
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="0"
    )
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    response = client.post(
        "/api/logs",
        files=[("files", ("too_big.txt", raw, "text/plain"))],
        headers=CREATOR_HEADERS,
    )
    assert response.status_code == 200
    results = response.json()
    assert results[0]["status"] == "error"
    assert "message" in results[0]


@pytest.mark.asyncio
async def test_ingest_splits_merged_target(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log

    raw = GAMELOG_HEADER + (
        b"[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        b"Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact Remote Capacitor Transmitter\n"
    )

    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        await session.flush()
        await ingest_log(session, get_settings(), "u", "Listener_20260614_205700_90000001.txt",
                         raw, lambda n: 90000001)
        await session.commit()

    async with db_session_maker() as session:
        ev = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "cap_transfer")
        )).scalars().first()
    assert ev is not None
    assert ev.other_ship_name == "Guardian"
    assert ev.other_name == "Jennifer Hibra"
