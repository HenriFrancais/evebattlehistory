from __future__ import annotations

import asyncio
import sqlite3

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.migrate import (
    LATEST_VERSION,
    existing_db_version,
    run_migrations,
    snapshot_before_migrate,
)
from app.db.models import Base
from app.observability.logging import log

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _register_pragma_listener(engine: AsyncEngine) -> None:
    """Register a connect-event listener so PRAGMAs run on every new connection."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn: sqlite3.Connection, connection_record: object) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Wait up to 30s for a held write lock instead of failing with SQLITE_BUSY
        # ("database is locked"). WAL permits concurrent readers but only one writer;
        # a background ingest can hold the writer for several seconds, so a quick op
        # (a delete, a source edit) that lands mid-ingest must wait it out rather than
        # 500. At 5s such ops failed and rolled back — e.g. a delete issued during an
        # ingest silently left the BR in place. Ingest itself is serialized in
        # jobs.schedule_ingest, so this timeout only backstops cross-operation contention.
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{settings.db_path}"
        _engine = create_async_engine(url, echo=False)
        _register_pragma_listener(_engine)
    return _engine


def get_sessionmaker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        engine = get_engine(settings)
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker


async def init_models(settings: Settings) -> None:
    """Create missing tables, then bring an existing database up to the latest
    schema version (see app/db/migrate.py). A database with pending migrations
    is snapshotted first."""
    version = existing_db_version(settings.db_path)
    if version is not None and version < LATEST_VERSION:
        # Copying a multi-hundred-MB database is blocking I/O: run it in a thread so
        # the event loop (and gunicorn's worker heartbeat) keeps turning at startup.
        await asyncio.to_thread(snapshot_before_migrate, settings.db_path, version)
    engine = get_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_migrations, version)
    log.info("db.models_initialized", from_version=version, version=LATEST_VERSION)


def reset_engine_for_tests() -> None:
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
