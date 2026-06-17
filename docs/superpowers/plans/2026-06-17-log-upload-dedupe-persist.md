# Log Upload (Bulk + Dedupe) + Persist + Character Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GamelogFile + LogEvent DB tables, content-addressed SHA-256 disk storage with dedupe, an ingest function that parses and resolves the owning character, and a bulk-upload API (POST /api/logs + GET /api/logs/mine).

**Architecture:** New files follow the existing module split: DB models extend `app/db/models.py`, storage validation lives in `app/logs/store.py`, persist/resolve lives in `app/logs/ingest.py`, and the FastAPI router lives in `app/api/logs.py`. The roster_lookup callable is built from `RosterSnapshot.name_to_char_id` (lowercased name → char_id). Dedupe is enforced at the ingest layer via a UNIQUE constraint on `sha256` — checked *before* any parse or disk write.

**Tech Stack:** FastAPI, SQLAlchemy 2 async + aiosqlite, structlog `log`, `from __future__ import annotations`, ruff line-length 100, mypy --strict, pytest-asyncio (asyncio_mode="auto"), python-multipart (already a dep).

## Global Constraints

- `from __future__ import annotations` at the top of every new file.
- ruff line-length 100 (`uv run ruff check .` must pass).
- mypy --strict (`uv run mypy app` must pass).
- All tests use `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.
- DB fixture: use the existing `db_session_maker` fixture from `tests/conftest.py` (provides an async sessionmaker backed by a temp SQLite DB with models already initialized).
- NO network: `DATA_SOURCE=demo`, `NV_TOKEN=test-token` everywhere.
- Dedupe by sha256 is mandatory and explicitly tested.
- Bulk upload: one request, many files, per-file results, batch never aborts.
- Unresolved logs are stored + shown (flagged), not discarded.
- Do NOT build fight association / coverage / buckets (Task 2.3) or frontend (Task 2.4).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `app/db/models.py` | Add `GamelogFile` + `LogEvent` ORM models |
| Create | `app/logs/store.py` | Validate upload + SHA-256 + write to disk |
| Create | `app/logs/ingest.py` | Dedupe check + parse + resolve + bulk-insert |
| Create | `app/api/logs.py` | POST /api/logs, GET /api/logs/mine |
| Modify | `app/main.py` | Register logs router |
| Create | `tests/test_log_ingest.py` | All ingest + API tests (TDD) |

---

### Task 1: DB Models (`app/db/models.py`)

**Files:**
- Modify: `app/db/models.py` (append after line 311)

**Interfaces:**
- Produces:
  - `GamelogFile` ORM model (table `gamelog_file`)
  - `LogEvent` ORM model (table `log_event`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_log_ingest.py`:

```python
"""TDD tests for Task 2.2: log upload, dedupe, persist, character resolution."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_gamelog_file_table_exists(db_session_maker):
    """GamelogFile table must exist after init_models."""
    async with db_session_maker() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result.fetchall()}
    assert "gamelog_file" in tables
    assert "log_event" in tables
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py::test_gamelog_file_table_exists -v
```

Expected: FAIL — `AssertionError: assert 'gamelog_file' in {...}`

- [ ] **Step 3: Add models to `app/db/models.py`**

Append the following after the last class (`BrShipCount`) in `app/db/models.py`:

```python
# ---------------------------------------------------------------------------
# Gamelog tables (Task 2.2)
# ---------------------------------------------------------------------------


class GamelogFile(Base):
    __tablename__ = "gamelog_file"

    file_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uploaded_by_user: Mapped[str] = mapped_column(String(128))
    claimed_character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    listener_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_via: Mapped[str] = mapped_column(String(32))  # "filename"|"listener_roster"|"unresolved"
    session_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_start_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_end_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(16))  # "parsed"|"unresolved"|"error"
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_gamelog_file_uploaded_by_user", "uploaded_by_user"),
        Index("ix_gamelog_file_sha256", "sha256"),
    )


class LogEvent(Base):
    __tablename__ = "log_event"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gamelog_file.file_id", ondelete="CASCADE", **_FK),  # type: ignore[arg-type]
    )
    character_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str | None] = mapped_column(String(4), nullable=True)
    effect_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    other_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    other_corp_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    other_alliance_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    other_ship_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    module_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fight_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # filled by Task 2.3

    __table_args__ = (
        Index("ix_log_event_character_id_ts", "character_id", "ts"),
        Index("ix_log_event_file_id", "file_id"),
        Index("ix_log_event_fight_id", "fight_id"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py::test_gamelog_file_table_exists -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add app/db/models.py tests/test_log_ingest.py && git commit -m "feat: add GamelogFile + LogEvent ORM models (Task 2.2)"
```

---

### Task 2: Storage Validation (`app/logs/store.py`)

**Files:**
- Create: `app/logs/store.py`

**Interfaces:**
- Consumes: `settings.log_dir` (Path), `settings.max_log_mb` (int)
- Produces:
  - `class StoreResult(NamedTuple): sha256: str; stored_path: Path; size: int; mime: str`
  - `def validate_and_store(raw_bytes: bytes, settings: Settings) -> StoreResult` — raises `ValueError` on validation failure
  - Validation rules: size ≤ `max_log_mb * 1024 * 1024`; content must start with a `Gamelog` header block (the `----\n  Gamelog` pattern); MIME is always `"text/plain"`.
  - File stored at `settings.log_dir / f"{sha256}.txt"` (content-addressed). If the file already exists on disk, skip writing (idempotent).

- [ ] **Step 1: Add store tests to `tests/test_log_ingest.py`**

```python
import hashlib
from pathlib import Path

from app.config import Settings
from app.logs.store import StoreResult, validate_and_store

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"

GAMELOG_HEADER = b"------------------------------------------------------------\n  Gamelog\n  Listener: TestChar Alpha\n  Session Started: 2026.06.16 19:21:14\n------------------------------------------------------------\n"


def _settings_with_tmp(tmp_path: Path) -> Settings:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return Settings(
        db_path=tmp_path / "test.db",
        log_dir=log_dir,
        max_log_mb=20,
        data_source="demo",
        nv_token="test-token",
    )


def test_validate_and_store_writes_file(tmp_path):
    settings = _settings_with_tmp(tmp_path)
    raw = GAMELOG_HEADER + b"[ 2026.06.16 19:21:15 ] (hint) some line\n"
    result = validate_and_store(raw, settings)
    assert isinstance(result, StoreResult)
    assert result.mime == "text/plain"
    expected_sha = hashlib.sha256(raw).hexdigest()
    assert result.sha256 == expected_sha
    assert result.stored_path.exists()
    assert result.stored_path.read_bytes() == raw


def test_validate_and_store_oversize_rejected(tmp_path):
    settings = _settings_with_tmp(tmp_path)
    # Make settings with tiny 1-byte limit
    tiny_settings = Settings(
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        max_log_mb=0,  # 0MB → any content too big
        data_source="demo",
        nv_token="test-token",
    )
    import pytest
    with pytest.raises(ValueError, match="too large"):
        validate_and_store(GAMELOG_HEADER, tiny_settings)


def test_validate_and_store_non_gamelog_rejected(tmp_path):
    settings = _settings_with_tmp(tmp_path)
    import pytest
    with pytest.raises(ValueError, match="not a valid gamelog"):
        validate_and_store(b"This is not a gamelog file at all", settings)


def test_validate_and_store_idempotent_no_double_write(tmp_path):
    """Writing the same bytes twice must not raise; the second call is a no-op on disk."""
    settings = _settings_with_tmp(tmp_path)
    raw = GAMELOG_HEADER + b"line\n"
    r1 = validate_and_store(raw, settings)
    r2 = validate_and_store(raw, settings)
    assert r1.sha256 == r2.sha256
    # Only one file on disk
    files = list(settings.log_dir.glob("*.txt"))
    assert len(files) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -k "store" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.logs.store'`

- [ ] **Step 3: Create `app/logs/store.py`**

```python
"""Content-addressed gamelog storage.

Validates raw bytes (size ≤ max_log_mb, must be a Gamelog file), computes sha256,
and stores under log_dir/<sha256>.txt.  A second call with the same bytes is a
silent no-op (the file already exists).  Nothing is served from this directory.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from app.config import Settings
from app.observability.logging import log

_GAMELOG_MAGIC = b"----"
_GAMELOG_HEADER_MARKER = b"Gamelog"


class StoreResult(NamedTuple):
    sha256: str
    stored_path: Path
    size: int
    mime: str


def validate_and_store(raw_bytes: bytes, settings: Settings) -> StoreResult:
    """Validate *raw_bytes* as a Gamelog upload and persist to content-addressed storage.

    Raises ``ValueError`` with a human-readable message on:
    - oversize (> ``max_log_mb`` MB)
    - content that does not look like an EVE gamelog (no ``Gamelog`` header block)
    """
    max_bytes = settings.max_log_mb * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise ValueError(
            f"File too large: {len(raw_bytes)} bytes exceeds {settings.max_log_mb} MB limit"
        )

    # Must start with the "----...Gamelog" block
    first_512 = raw_bytes[:512]
    if _GAMELOG_MAGIC not in first_512 or _GAMELOG_HEADER_MARKER not in first_512:
        raise ValueError("not a valid gamelog: missing Gamelog header block")

    sha = hashlib.sha256(raw_bytes).hexdigest()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.log_dir / f"{sha}.txt"

    if not dest.exists():
        dest.write_bytes(raw_bytes)
        log.info("logs.store.written", sha256=sha, size=len(raw_bytes))
    else:
        log.debug("logs.store.already_exists", sha256=sha)

    return StoreResult(sha256=sha, stored_path=dest, size=len(raw_bytes), mime="text/plain")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -k "store" -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add app/logs/store.py tests/test_log_ingest.py && git commit -m "feat: add gamelog content-addressed storage (Task 2.2)"
```

---

### Task 3: Ingest + Dedupe + Character Resolution (`app/logs/ingest.py`)

**Files:**
- Create: `app/logs/ingest.py`

**Interfaces:**
- Consumes:
  - `app.logs.store.validate_and_store(raw_bytes, settings) -> StoreResult`
  - `app.logs.parse.parse_log(text) -> ParsedLog`
  - `app.logs.filename.parse_filename(name) -> dict`, `parse_header(text) -> LogHeader`, `resolve_character(filename_meta, header, roster_lookup) -> dict`
  - `app.db.models.GamelogFile`, `app.db.models.LogEvent`
  - `app.roster.snapshot.get_roster_store(settings) -> RosterStore` (call `.get()` to get snapshot, use `.name_to_char_id` for lookup)
- Produces:
  - `class GamelogFileResult(NamedTuple): file_id: int; duplicate: bool; parse_status: str; event_count: int; character_id: int | None; character_name: str | None; listener_name: str | None`
  - `async def ingest_log(session, settings, uploaded_by_user, filename, raw_bytes) -> GamelogFileResult`

**Dedupe rule:** compute sha256 of `raw_bytes` *first* (before any parse or disk write). If a `GamelogFile` row with that sha256 already exists, return `GamelogFileResult(file_id=existing.file_id, duplicate=True, ...)` immediately.

- [ ] **Step 1: Add ingest tests to `tests/test_log_ingest.py`**

```python
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.ingest import GamelogFileResult, ingest_log
from app.roster.snapshot import build_roster_snapshot
from app.roster.models import RosterUser, RosterCharacter

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"

def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        max_log_mb=20,
        data_source="demo",
        nv_token="test-token",
    )


def _make_roster_lookup(name_to_id: dict[str, int]):
    """Build a roster_lookup callable from a name→id dict (lowercase keys)."""
    def lookup(name: str) -> int | None:
        return name_to_id.get(name.lower())
    return lookup


@pytest.mark.asyncio
async def test_ingest_log_parses_and_persists(tmp_path, db_session_maker):
    """Ingest a real fixture log; verify GamelogFile row + N LogEvent rows created."""
    settings = _make_settings(tmp_path)
    raw = (FIXTURES / "full_fight.txt").read_bytes()
    filename = "20260616_192114_2112615087.txt"

    async with db_session_maker() as session:
        result = await ingest_log(
            session, settings,
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
        gf = (await session.execute(
            select(GamelogFile).where(GamelogFile.file_id == result.file_id)
        )).scalar_one()
        event_count_db = (await session.execute(
            select(func.count()).select_from(LogEvent).where(LogEvent.file_id == result.file_id)
        )).scalar()

    assert gf.uploaded_by_user == "Ra'zok"
    assert gf.claimed_character_id == 2112615087
    assert gf.resolved_via == "filename"
    assert gf.parse_status == "parsed"
    assert gf.event_count == result.event_count
    assert event_count_db == result.event_count


@pytest.mark.asyncio
async def test_ingest_log_dedupe(tmp_path, db_session_maker):
    """Re-uploading the same bytes must return duplicate=True with NO new rows."""
    settings = _make_settings(tmp_path)
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
        gf_count = (await session.execute(
            select(func.count()).select_from(GamelogFile)
        )).scalar()
        ev_count = (await session.execute(
            select(func.count()).select_from(LogEvent)
        )).scalar()

    assert gf_count == 1  # Only one GamelogFile row
    assert ev_count == r1.event_count  # No duplicate events


@pytest.mark.asyncio
async def test_ingest_log_unresolved(tmp_path, db_session_maker):
    """A log with no charId and unknown listener name: stored, status=unresolved, events persisted."""
    settings = _make_settings(tmp_path)
    raw = (FIXTURES / "no_char_id.txt").read_bytes()
    filename = "20231006_204512.txt"  # no char_id in filename

    async with db_session_maker() as session:
        result = await ingest_log(
            session, settings,
            uploaded_by_user="LineMember",
            filename=filename,
            raw_bytes=raw,
            roster_lookup=_make_roster_lookup({}),  # empty roster → unresolved
        )
        await session.commit()

    assert result.duplicate is False
    assert result.parse_status == "unresolved"
    assert result.character_id is None

    async with db_session_maker() as session:
        gf = (await session.execute(
            select(GamelogFile).where(GamelogFile.file_id == result.file_id)
        )).scalar_one()
        ev_count = (await session.execute(
            select(func.count()).select_from(LogEvent).where(LogEvent.file_id == result.file_id)
        )).scalar()

    assert gf.parse_status == "unresolved"
    assert ev_count == result.event_count  # Events still persisted


@pytest.mark.asyncio
async def test_ingest_log_error_on_bad_content(tmp_path, db_session_maker):
    """Non-gamelog content results in parse_status=error, no events."""
    settings = _make_settings(tmp_path)
    # This will fail store validation
    import pytest
    with pytest.raises(ValueError):
        async with db_session_maker() as session:
            await ingest_log(
                session, settings,
                uploaded_by_user="Ra'zok",
                filename="corrupt.txt",
                raw_bytes=b"not a gamelog",
                roster_lookup=_make_roster_lookup({}),
            )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -k "ingest" -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.logs.ingest'`

- [ ] **Step 3: Read the roster models to confirm RosterUser/RosterCharacter fields**

```bash
cat /home/matron/dev/nv-wh-fight-history/app/roster/models.py
```

- [ ] **Step 4: Create `app/logs/ingest.py`**

```python
"""Gamelog ingest: dedupe + validate + store + parse + resolve + bulk-insert events.

Public API
----------
    result = await ingest_log(session, settings, uploaded_by_user, filename, raw_bytes, roster_lookup)

Dedupe contract: sha256 is checked *first*.  If a GamelogFile row with that sha256
already exists, returns immediately with ``duplicate=True`` — no parse, no disk write,
no new events.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.filename import parse_filename, resolve_character
from app.logs.parse import parse_log
from app.logs.store import validate_and_store
from app.observability.logging import log


class GamelogFileResult(NamedTuple):
    file_id: int
    duplicate: bool
    parse_status: str
    event_count: int
    character_id: int | None
    character_name: str | None
    listener_name: str | None


async def ingest_log(
    session: AsyncSession,
    settings: Settings,
    uploaded_by_user: str,
    filename: str,
    raw_bytes: bytes,
    roster_lookup: Callable[[str], int | None],
) -> GamelogFileResult:
    """Parse, resolve, and persist a single gamelog upload.

    Parameters
    ----------
    session:
        An open async SQLAlchemy session (caller is responsible for commit).
    settings:
        App settings (provides log_dir, max_log_mb).
    uploaded_by_user:
        The authenticated user's user_name.
    filename:
        The original filename (used for parse_filename to extract char_id and session start).
    raw_bytes:
        Raw file content.
    roster_lookup:
        Callable(name: str) -> int | None  — maps a listener name to a character_id.
        Build from RosterSnapshot.name_to_char_id: ``lambda n: snap.name_to_char_id.get(n.lower())``.

    Returns
    -------
    GamelogFileResult
        ``duplicate=True`` when sha256 already exists in DB (no side effects performed).
    """
    # 1. Dedupe check: compute sha256 before touching disk or DB
    sha = hashlib.sha256(raw_bytes).hexdigest()
    existing = (
        await session.execute(select(GamelogFile).where(GamelogFile.sha256 == sha))
    ).scalar_one_or_none()

    if existing is not None:
        log.info("logs.ingest.duplicate", sha256=sha, file_id=existing.file_id)
        return GamelogFileResult(
            file_id=existing.file_id,
            duplicate=True,
            parse_status=existing.parse_status,
            event_count=existing.event_count,
            character_id=existing.claimed_character_id,
            character_name=existing.listener_name,
            listener_name=existing.listener_name,
        )

    # 2. Validate + store (raises ValueError on bad content or oversize)
    store_result = validate_and_store(raw_bytes, settings)

    # 3. Parse
    text = raw_bytes.decode("utf-8", errors="replace")
    parsed = parse_log(text)

    # 4. Resolve owning character
    filename_meta = parse_filename(filename)
    char_info = resolve_character(filename_meta, parsed.header, roster_lookup)
    character_id: int | None = char_info["character_id"]
    character_name: str | None = char_info["character_name"]
    resolved_via: str = char_info["resolved_via"]

    # 5. Determine parse_status
    parse_status = "unresolved" if character_id is None else "parsed"

    # 6. Derive log time bounds from events
    event_ts_list = [e.ts for e in parsed.events]
    log_start_at = min(event_ts_list) if event_ts_list else None
    log_end_at = max(event_ts_list) if event_ts_list else None

    # 7. Insert GamelogFile row
    now = dt.datetime.now(dt.UTC)
    session_started_at = parsed.header.session_started

    gf = GamelogFile(
        uploaded_by_user=uploaded_by_user,
        claimed_character_id=character_id,
        listener_name=parsed.header.listener_name,
        resolved_via=resolved_via,
        session_started_at=session_started_at,
        log_start_at=log_start_at,
        log_end_at=log_end_at,
        stored_path=str(store_result.stored_path),
        sha256=store_result.sha256,
        mime=store_result.mime,
        size=store_result.size,
        parse_status=parse_status,
        event_count=len(parsed.events),
        uploaded_at=now,
    )
    session.add(gf)
    await session.flush()  # Get file_id assigned

    # 8. Bulk-insert LogEvent rows
    if parsed.events:
        events = [
            LogEvent(
                file_id=gf.file_id,
                character_id=character_id,
                ts=e.ts,
                direction=e.direction,
                effect_type=e.effect_type,
                amount=e.amount,
                quality=e.quality,
                other_name=e.other_name,
                other_corp_ticker=e.other_corp_ticker,
                other_alliance_ticker=e.other_alliance_ticker,
                other_ship_name=e.other_ship_name,
                module_name=e.module_name,
                fight_id=None,
            )
            for e in parsed.events
        ]
        session.add_all(events)

    log.info(
        "logs.ingest.persisted",
        file_id=gf.file_id,
        sha256=sha,
        parse_status=parse_status,
        events=len(parsed.events),
    )

    return GamelogFileResult(
        file_id=gf.file_id,
        duplicate=False,
        parse_status=parse_status,
        event_count=len(parsed.events),
        character_id=character_id,
        character_name=character_name,
        listener_name=parsed.header.listener_name,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -k "ingest" -v
```

Expected: 4 PASS (including `test_ingest_log_dedupe` — the critical dedupe test)

- [ ] **Step 6: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add app/logs/ingest.py tests/test_log_ingest.py && git commit -m "feat: add gamelog ingest with sha256 dedupe + character resolution (Task 2.2)"
```

---

### Task 4: Upload API (`app/api/logs.py`) + Router Registration

**Files:**
- Create: `app/api/logs.py`
- Modify: `app/main.py` (import + register router)

**Interfaces:**
- Consumes:
  - `ingest_log(session, settings, uploaded_by_user, filename, raw_bytes, roster_lookup) -> GamelogFileResult`
  - `get_roster_store(settings) -> RosterStore` → `.get() -> RosterSnapshot` → `.name_to_char_id`
  - `current_user(request) -> CurrentUser` → `.user_name`
  - `app.db.models.GamelogFile`
- Produces:
  - `POST /api/logs` — list of per-file result dicts
  - `GET /api/logs/mine` — list of uploaded log summaries for the current user

**POST /api/logs response schema per file:**
```json
{"filename": "...", "file_id": 1, "status": "parsed|unresolved|duplicate|error", "event_count": 42, "character_name": "...", "message": "..."}
```

**GET /api/logs/mine response schema per file:**
```json
{"file_id": 1, "filename": null, "character_id": null, "character_name": null, "listener_name": null, "parse_status": "parsed", "event_count": 42, "log_start_at": null, "log_end_at": null, "uploaded_at": "..."}
```
Note: `filename` is not stored in DB (only the sha256 path). Return `null` for filename in GET /mine.

- [ ] **Step 1: Add API tests to `tests/test_log_ingest.py`**

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"

# Reuse conftest fixtures: make_client, CREATOR_HEADERS, MEMBER_HEADERS
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS


def test_post_logs_requires_auth(client):
    """Unauthenticated upload → 401."""
    response = client.post("/api/logs", files=[])
    assert response.status_code == 401


def test_post_logs_single_file_parsed(make_client, tmp_path):
    """Upload one valid file → response list with status=parsed."""
    import os
    client = make_client(LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20")
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
    assert r["status"] in ("parsed", "unresolved")  # may be unresolved if roster empty in demo
    assert r["event_count"] > 0
    assert r["filename"] == "20260616_192114_2112615087.txt"


def test_post_logs_dedupe_in_batch(make_client, tmp_path):
    """Uploading the same file twice in one batch: second entry is duplicate."""
    client = make_client(LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20")
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


def test_post_logs_corrupt_file_error_no_abort(make_client, tmp_path):
    """A corrupt file in a batch does not abort; returns error status for that file."""
    client = make_client(LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20")
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


def test_get_logs_mine_returns_only_caller_files(make_client, tmp_path):
    """GET /api/logs/mine scopes by user: each user sees only their own uploads."""
    client = make_client(LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="20")

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


def test_post_logs_oversize_rejected_per_file(make_client, tmp_path):
    """Files exceeding max_log_mb are reported as error (not batch abort)."""
    client = make_client(LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="0")
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -k "api or logs_mine or post_logs or get_logs" -v
```

Expected: FAIL — `404` or `AttributeError` since the router isn't registered yet.

- [ ] **Step 3: Create `app/api/logs.py`**

```python
"""FastAPI router for gamelog bulk upload and personal log history.

POST /api/logs  — accepts many files, returns per-file result list.
GET  /api/logs/mine — the caller's uploaded logs, newest first.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user
from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.db.models import GamelogFile
from app.logs.ingest import GamelogFileResult, ingest_log
from app.observability.logging import log
from app.roster.snapshot import get_roster_store

router = APIRouter()


async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    settings = get_settings()
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        yield session


async def _build_roster_lookup() -> Callable[[str], int | None]:
    settings = get_settings()
    try:
        roster = await get_roster_store(settings).get()
        name_to_id = roster.name_to_char_id  # already lowercase
        return lambda name: name_to_id.get(name.strip().lower())
    except Exception as exc:
        log.warning("logs.roster_lookup_failed", error=str(exc))
        return lambda name: None


@router.post("/api/logs")
async def upload_logs(
    request: Request,
    files: list[UploadFile],
) -> list[dict[str, Any]]:
    """Bulk upload gamelog files.  Per-file results; never aborts on one bad file."""
    user = current_user(request)
    settings = get_settings()
    session_maker = get_sessionmaker(settings)
    roster_lookup = await _build_roster_lookup()

    results: list[dict[str, Any]] = []

    for upload in files:
        filename = upload.filename or "unknown.txt"
        try:
            raw_bytes = await upload.read()
            async with session_maker() as session:
                result: GamelogFileResult = await ingest_log(
                    session=session,
                    settings=settings,
                    uploaded_by_user=user.user_name,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    roster_lookup=roster_lookup,
                )
                await session.commit()

            status = "duplicate" if result.duplicate else result.parse_status
            results.append({
                "filename": filename,
                "file_id": result.file_id,
                "status": status,
                "event_count": result.event_count,
                "character_name": result.character_name,
                "message": None,
            })
        except Exception as exc:
            log.warning("logs.upload.file_error", filename=filename, error=str(exc))
            results.append({
                "filename": filename,
                "file_id": None,
                "status": "error",
                "event_count": 0,
                "character_name": None,
                "message": str(exc),
            })

    return results


@router.get("/api/logs/mine")
async def get_my_logs(request: Request) -> list[dict[str, Any]]:
    """Return the current user's uploaded logs, newest first."""
    user = current_user(request)
    settings = get_settings()
    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        result = await session.execute(
            select(GamelogFile)
            .where(GamelogFile.uploaded_by_user == user.user_name)
            .order_by(GamelogFile.uploaded_at.desc())
        )
        files = list(result.scalars())

    return [
        {
            "file_id": f.file_id,
            "filename": None,  # original filename not stored; only sha256 path
            "character_id": f.claimed_character_id,
            "character_name": f.listener_name,
            "listener_name": f.listener_name,
            "parse_status": f.parse_status,
            "event_count": f.event_count,
            "log_start_at": f.log_start_at.isoformat() if f.log_start_at else None,
            "log_end_at": f.log_end_at.isoformat() if f.log_end_at else None,
            "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        }
        for f in files
    ]
```

- [ ] **Step 4: Register the router in `app/main.py`**

In `app/main.py`, add the import and `include_router` call. After the existing imports add:

```python
from app.api.logs import router as logs_router
```

And in `create_app()` after `app.include_router(brs_router, prefix=prefix)`:

```python
app.include_router(logs_router, prefix=prefix)
```

- [ ] **Step 5: Add LOG_DIR and MAX_LOG_MB to Settings env mapping**

Check `app/config.py` — `log_dir` and `max_log_mb` already exist. But `make_client` passes `LOG_DIR` as an env var. Confirm the pydantic-settings env var names: they map `log_dir` → `LOG_DIR` automatically. No change needed.

- [ ] **Step 6: Run all API tests**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_log_ingest.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Run full suite + linters**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest -v && uv run ruff check . && uv run mypy app
```

Expected: all tests pass, no ruff errors, mypy clean.

- [ ] **Step 8: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add app/api/logs.py app/main.py tests/test_log_ingest.py && git commit -m "feat: add bulk upload API POST /api/logs + GET /api/logs/mine (Task 2.2)"
```

---

## Spec Coverage Self-Check

| Requirement | Task |
|---|---|
| GamelogFile table with all specified columns, UNIQUE sha256 | Task 1 |
| LogEvent table with all specified columns and indexes | Task 1 |
| Content-addressed disk storage (sha256 + `.txt`) | Task 2 |
| Validate size ≤ max_log_mb | Task 2 |
| Reject non-gamelog content | Task 2 |
| SHA-256 dedupe: re-upload same bytes → duplicate, no new rows | Task 3 |
| Parse via parse_log + resolve via resolve_character | Task 3 |
| Roster lookup from name_to_char_id | Task 3 |
| Derive log_start_at/log_end_at from event timestamps | Task 3 |
| Bulk-insert LogEvent rows with character_id | Task 3 |
| Unresolved logs: stored + parse_status=unresolved, events persisted | Task 3 |
| POST /api/logs accepts many files, per-file results | Task 4 |
| Batch never aborts on one bad file | Task 4 |
| Duplicate status in response | Task 4 |
| GET /api/logs/mine scoped by user_name | Task 4 |
| Auth required (401 without token) | Task 4 |
| Register router in app/main.py | Task 4 |
| Tests: ingest fixture, dedupe (explicit), unresolved, bulk POST, user isolation, oversize | Tasks 3+4 |

## Notes / Watchpoints

1. **`db_session_maker` fixture** provides an async sessionmaker with models already initialized — ingest tests must use it (not raw `Settings` + `init_models`). The `ingest_log` function takes a `session` directly, so tests open their own session from the fixture and commit after.

2. **`make_client` env overrides** — `LOG_DIR` maps to `Settings.log_dir` via pydantic-settings automatic env var naming. Test should pass `LOG_DIR=str(tmp_path / "logs")` to avoid writing to the real log dir.

3. **roster_lookup in API** — the API calls `get_roster_store(settings).get()`. In demo mode this uses the demo roster source, which may return an empty roster. Tests that care about character resolution bypass the API and test `ingest_log` directly with an explicit `roster_lookup`.

4. **`damage_in.txt` fixture** used in the user-isolation test — verify it is a valid gamelog file with the `Gamelog` header block. If not, substitute with `with_char_id.txt`.

5. **mypy strict** — all `dict[str, Any]` return types in `app/api/logs.py` need explicit `Any` imports. `GamelogFileResult` is a `NamedTuple` so mypy handles it well.
