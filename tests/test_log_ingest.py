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


async def test_log_event_has_ewar_attribution_columns(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """LogEvent must expose source_name, target_name, authoritative, dedupe_suppressed."""
    from sqlalchemy import inspect as sa_inspect

    from app.config import get_settings
    from app.db.engine import get_engine

    engine = get_engine(get_settings())
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sc: {c["name"] for c in sa_inspect(sc).get_columns("log_event")}
        )
    assert {"source_name", "target_name", "authoritative", "dedupe_suppressed"} <= cols


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


_THIRD_PARTY_SCRAM_LOG_BYTES = GAMELOG_HEADER + (
    b"[ 2026.01.01 12:06:44 ] (combat) "
    b"<color=0xffffffff><b>Warp scramble attempt</b> "
    b"<color=0x77ffffff><font size=10>from</font> "
    b"<color=0xffffffff><b>"
    b"<font size=12><color=0xFFFFFFFF><b>AllyChar Kyte</b> </color></font>"
    b"<font size=12><color=0xFFFFB300>[NV]</color></font>"
    b"<font size=12>[NVACA]</font> "
    b"<font size=12><color=0xFFFFFFFF><b>Muninn</b></color></font></b> "
    b"<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
    b"<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
    b"<font size=12><color=0xFFFFB300>[10MN]</color></font>"
    b"<font size=12>[.EFG]</font> "
    b"<font size=12><color=0xFFFFFFFF><b>Omen Navy Issue</b></color></font>\n"
)


@pytest.mark.asyncio
async def test_ingest_third_party_scram_attributes_real_tackler(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import LogEvent
    from app.logs.ingest import ingest_log

    raw = _THIRD_PARTY_SCRAM_LOG_BYTES
    async with db_session_maker() as session:
        await ingest_log(
            session, get_settings(), "Ra'zok", "20260101_120000_2112615087.txt", raw,
            _make_roster_lookup({"testchar alpha": 2112615087}),
        )
        await session.commit()

    async with db_session_maker() as session:
        row = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "scram")
        )).scalar_one()
    assert row.authoritative is False
    assert row.source_name == "AllyChar Kyte"
    assert row.target_name == "FakeEnemy Delta"
    assert row.other_name == "AllyChar Kyte"   # real tackler
    assert row.character_id == 2112615087       # owner column still the file owner


_EWAR_SOURCE_TARGET_RAW_LOG = GAMELOG_HEADER + (
    b"[ 2026.06.14 20:57:21 ] (combat) "
    b"<color=0xffffffff><b>Warp disruption attempt</b> "
    b"<color=0x77ffffff><font size=10>from</font> "
    b"<color=0xffffffff><b>"
    b"<font size=11><color=\"orange\"><b>Proteus</b></color></font> "
    b"<font size=9><color=\"yellow\">Nate Marston</color></font> "
    b"<font size=8><color=\"0xFF00FFFF\">[NVACA] </color></font>"
    b"<font size=8><color=\"0xFF00FFFF\">&lt;NV&gt; </color></font>"
    b"</b> "
    b"<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
    b"<font size=11><color=\"orange\"><b>Leshak</b></color></font> "
    b"<font size=9><color=\"yellow\">Tom-w</color></font> "
    b"<font size=8><color=\"0xFF00FFFF\">[OMGGF] </color></font>"
    b"<font size=8><color=\"0xFF00FFFF\">&lt;LUPUS&gt; </color></font>\n"
)


@pytest.mark.asyncio
async def test_ingest_cleans_source_and_target_names(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Bug (B): source_name/target_name must be SDE-cleaned (char name only, no ship/corp).

    Raw source: "Proteus Nate Marston [NVACA] <NV>" → should become "Nate Marston"
    Raw target: "Leshak Tom-w [OMGGF] <LUPUS>"    → should become "Tom-w"

    Before the fix, ingest.py did NOT apply split_entity to source_name/target_name,
    so raw entity strings with ship type prefixes were stored verbatim.
    """
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log

    raw = _EWAR_SOURCE_TARGET_RAW_LOG
    async with db_session_maker() as session:
        # Register Proteus and Leshak as known ship types so split_entity can split them
        session.add(InventoryType(type_id=2, name="Proteus", category_id=6))
        session.add(InventoryType(type_id=3, name="Leshak", category_id=6))
        await session.flush()
        await ingest_log(
            session,
            get_settings(),
            "Ra'zok",
            "20260614_205721_2112615087.txt",
            raw,
            _make_roster_lookup({"testchar alpha": 2112615087}),
        )
        await session.commit()

    async with db_session_maker() as session:
        row = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "disrupt")
        )).scalar_one()

    assert row.source_name == "Nate Marston", (
        f"Expected source_name='Nate Marston' but got {row.source_name!r}. "
        "Fix: apply split_entity to source_name in ingest.py"
    )
    assert row.target_name == "Tom-w", (
        f"Expected target_name='Tom-w' but got {row.target_name!r}. "
        "Fix: apply split_entity to target_name in ingest.py"
    )


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


# ---------------------------------------------------------------------------
# Custom (user-entered) ship names: EVE renders a named ship's cosmetic name in
# <i>..</i>; it must NOT be attributed as the pilot. The real pilot is still in a
# trailing [bracket] and must be recovered. These are real lines from fight 8 of
# the 2026-06-14 31002150 BR (with custom names like "[I] Nurse Sarah", "+[BDA] DPS").
# ---------------------------------------------------------------------------

_NAMED_SHIP_NEUT_LOG = GAMELOG_HEADER + (
    b"[ 2026.06.14 21:01:59 ] (combat) <color=0xff7fffff><b>144 GJ</b><color=0x77ffffff>"
    b"<font size=10> energy neutralized </font><b><color=0xffffffff><fontsize=12>"
    b"<color=0xFFFEBB64><b> <u>Nestor</u></b></color></fontsize> <i>[I] Nurse Sarah</i>]"
    b"</b></fontsize><fontsize=10> [Izmaragd Dawnstar]</fontsize><color=0xFFFFFFFF><b> -"
    b"<fontsize=12><color=0xFFFEFF6F> [ECHO.]</color></fontsize></b><color=0x77ffffff>"
    b"<font size=10> - Medium Energy Neutralizer II</font>\n"
)

_NAMED_SHIP_SCRAM_LOG = GAMELOG_HEADER + (
    b"[ 2026.06.14 18:18:32 ] (combat) <color=0xffffffff><b>Warp scramble attempt</b> "
    b"<color=0x77ffffff><font size=10>from</font> <color=0xffffffff><b><fontsize=12>"
    b"<color=0xFFFEBB64><b> <u>Legion</u></b></color></fontsize> <i>+[BDA] DPS</i>]"
    b"</b></fontsize><fontsize=10> [Ra'zok Zateki]</fontsize><color=0xFFFFFFFF><b> -"
    b"<fontsize=12><color=0xFFFEFF6F> [NV]</color></fontsize></b> <color=0x77ffffff>"
    b"<font size=10>to <b><color=0xffffffff></font><fontsize=12><color=0xFFFEBB64><b> "
    b"<u>Huginn</u></b></color></fontsize> <i>Famous Mockingbird</i>]</b></fontsize>"
    b"<fontsize=10> [Sue Moe]</fontsize><color=0xFFFFFFFF><b> -<fontsize=12>"
    b"<color=0xFFFEFF6F> [HIGH.]</color></fontsize>\n"
)


@pytest.mark.asyncio
async def test_ingest_custom_named_ship_other_name_is_pilot(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A neut on a custom-named Nestor must attribute the pilot (Izmaragd Dawnstar),
    not the cosmetic ship name ("Nurse Sarah")."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log

    async with db_session_maker() as session:
        session.add(InventoryType(type_id=33472, name="Nestor", category_id=6))
        await session.flush()
        await ingest_log(session, get_settings(), "u", "L_20260614_210000_90000001.txt",
                         _NAMED_SHIP_NEUT_LOG, lambda n: 90000001)
        await session.commit()

    async with db_session_maker() as session:
        ev = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "neut")
        )).scalar_one()
    assert ev.other_name == "Izmaragd Dawnstar", (
        f"custom ship name leaked: other_name={ev.other_name!r}"
    )
    assert ev.other_ship_name == "Nestor"


@pytest.mark.asyncio
async def test_ingest_custom_named_ship_ewar_source_target_are_pilots(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A third-party scram between two custom-named ships must record both real pilots
    (Ra'zok Zateki / Sue Moe), never the cosmetic names ("+[BDA] DPS" / "Famous Mockingbird")."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log

    async with db_session_maker() as session:
        session.add(InventoryType(type_id=29986, name="Legion", category_id=6))
        session.add(InventoryType(type_id=11961, name="Huginn", category_id=6))
        await session.flush()
        await ingest_log(session, get_settings(), "u", "L_20260614_181800_90000002.txt",
                         _NAMED_SHIP_SCRAM_LOG, lambda n: 90000002)
        await session.commit()

    async with db_session_maker() as session:
        ev = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "scram")
        )).scalar_one()
    assert ev.source_name == "Ra'zok Zateki", f"source leaked: {ev.source_name!r}"
    assert ev.target_name == "Sue Moe", f"target leaked: {ev.target_name!r}"


# A bracket-form scram where the source pilot's FIRST NAME is itself a ship hull:
# "Wolf Hibra" flies a Heretic ("Wolf" is the assault-frigate hull). The parser
# already separates ship=Heretic from pilot="Wolf Hibra" via the [bracket]; ingest
# must NOT re-run split_entity on the clean pilot (which would peel "Wolf" and leave
# the bare surname "Hibra"). Real line from the 2026-06-26 NV-vs-MSF BR.
_SHIP_FIRSTNAME_SCRAM_LOG = GAMELOG_HEADER + (
    b"[ 2026.06.26 20:33:25 ] (combat) <color=0xffffffff><b>Warp scramble attempt</b> "
    b"<color=0x77ffffff><font size=10>from</font> <color=0xffffffff><b><fontsize=12>"
    b"<color=0xFFFEBB64><b> <u>Heretic</u></b></color></fontsize> <i>+[HA/Zerg] Heretic</i>]"
    b"</b></fontsize><fontsize=10> [Wolf Hibra]</fontsize><color=0xFFFFFFFF><b> -"
    b"<fontsize=12><color=0xFFFEFF6F> [NV]</color></fontsize></b> <color=0x77ffffff>"
    b"<font size=10>to <b><color=0xffffffff></font><fontsize=12><color=0xFFFEBB64><b> "
    b"<u>Outrider</u></b></color></fontsize> <i>Slightly Rerited</i>]</b></fontsize>"
    b"<fontsize=10> [SavageDoob Severasse]</fontsize><color=0xFFFFFFFF><b> -<fontsize=12>"
    b"<color=0xFFFEFF6F> [MSF.]</color></fontsize>\n"
)


@pytest.mark.asyncio
async def test_ingest_ewar_pilot_with_ship_firstname_not_split(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A tackler whose first name collides with a ship hull (Wolf Hibra in a Heretic,
    bracket form) must keep the full pilot name — not be truncated to the surname."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log

    async with db_session_maker() as session:
        # Register the colliding hulls so split_entity WOULD peel "Wolf" if applied.
        session.add(InventoryType(type_id=11371, name="Wolf", category_id=6))
        session.add(InventoryType(type_id=11393, name="Heretic", category_id=6))
        session.add(InventoryType(type_id=11192, name="Outrider", category_id=6))
        await session.flush()
        await ingest_log(session, get_settings(), "u", "L_20260626_203300_90000003.txt",
                         _SHIP_FIRSTNAME_SCRAM_LOG, lambda n: 90000003)
        await session.commit()

    async with db_session_maker() as session:
        ev = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "scram")
        )).scalar_one()
    assert ev.source_name == "Wolf Hibra", f"surname-only leak: {ev.source_name!r}"
    assert ev.target_name == "SavageDoob Severasse", f"target leaked: {ev.target_name!r}"


# ---------------------------------------------------------------------------
# Fix B: "you" as source resolves to owner character_name
# ---------------------------------------------------------------------------

_YOU_AS_SOURCE_LOG_BYTES = GAMELOG_HEADER + (
    b"[ 2026.01.01 12:06:44 ] (combat) "
    b"Warp scramble attempt from you to "
    b"FakeEnemy Delta [10MN][.EFG] Omen Navy Issue\n"
)


@pytest.mark.asyncio
async def test_ingest_you_as_source_resolves_to_owner(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Fix B: when the log owner is the tackle source ('you'), source_name should
    be set to the owner's character_name ('TestChar Alpha') rather than None."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import LogEvent
    from app.logs.ingest import ingest_log

    raw = _YOU_AS_SOURCE_LOG_BYTES
    async with db_session_maker() as session:
        await ingest_log(
            session, get_settings(), "Ra'zok", "20260101_120000_2112615087.txt", raw,
            _make_roster_lookup({"testchar alpha": 2112615087}),
        )
        await session.commit()

    async with db_session_maker() as session:
        row = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "scram")
        )).scalar_one()

    assert row.authoritative is True
    assert row.source_name == "TestChar Alpha", (
        f"Expected source_name='TestChar Alpha' but got {row.source_name!r}. "
        "Fix B: authoritative 'you' source should resolve to character_name."
    )
    assert row.target_name == "FakeEnemy Delta"
