"""Versioned schema migrations, tracked in SQLite's ``PRAGMA user_version``.

``Base.metadata.create_all`` creates missing TABLES but never alters an existing
one, so a deploy that adds a column used to need a hand-run ``ALTER TABLE`` on the
live database. Instead, every schema change to an existing table is appended to
``MIGRATIONS`` and applied automatically at startup (see ``init_models``).

Rules for adding a migration:
  * append ``(next_version, description, fn)`` — never edit or reorder old entries;
  * ``fn`` receives a sync SQLAlchemy ``Connection`` inside the startup transaction;
  * make it idempotent (use ``add_column_if_missing``) — production databases had
    some columns added by hand before this runner existed.

A database that needs migrating is snapshotted first to
``<db>.pre-v<current_version>`` (SQLite online backup), so a bad migration can be
rolled back by restoring that file.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from sqlalchemy import Connection

from app.observability.logging import log


def add_column_if_missing(conn: Connection, table: str, column: str, ddl: str) -> bool:
    """``ALTER TABLE table ADD COLUMN column ddl`` unless it already exists."""
    existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def _m001_legacy_hand_altered_columns(conn: Connection) -> None:
    """Columns that were historically added to production by hand."""
    add_column_if_missing(conn, "killmail", "damage_taken", "INTEGER")
    add_column_if_missing(conn, "battle_report", "discord_thread_id", "BIGINT")
    add_column_if_missing(conn, "battle_report", "discord_thread_url", "TEXT")
    add_column_if_missing(conn, "log_event", "source_name", "VARCHAR(128)")
    add_column_if_missing(conn, "log_event", "target_name", "VARCHAR(128)")
    add_column_if_missing(conn, "log_event", "authoritative", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "log_event", "dedupe_suppressed", "BOOLEAN NOT NULL DEFAULT 0")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_log_event_ewar_dedupe "
        "ON log_event (fight_id, effect_type, source_name, target_name)"
    )


def _m002_ingest_completeness(conn: Connection) -> None:
    add_column_if_missing(conn, "battle_report", "km_expected", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "battle_report", "warning_text", "TEXT")
    # Existing BRs predate the distinction; assume they were complete.
    conn.exec_driver_sql("UPDATE battle_report SET km_expected = km_count WHERE km_expected = 0")


Migration = tuple[int, str, Callable[[Connection], None]]

MIGRATIONS: list[Migration] = [
    (1, "legacy hand-altered columns", _m001_legacy_hand_altered_columns),
    (2, "battle_report ingest completeness", _m002_ingest_completeness),
]

LATEST_VERSION: int = MIGRATIONS[-1][0]


def existing_db_version(db_path: Path) -> int | None:
    """``user_version`` of an already-populated database, or None for a fresh one
    (missing/empty file, or no app tables yet)."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    with closing(sqlite3.connect(str(db_path))) as c:
        has_tables = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='battle_report'"
        ).fetchone()
        if not has_tables:
            return None
        return int(c.execute("PRAGMA user_version").fetchone()[0])


def snapshot_before_migrate(db_path: Path, version: int) -> Path:
    """Copy the live DB to ``<db>.pre-v<version>`` with the online-backup API."""
    dest = db_path.with_name(f"{db_path.name}.pre-v{version}")
    with closing(sqlite3.connect(str(db_path))) as src, closing(
        sqlite3.connect(str(dest))
    ) as dst:
        src.backup(dst)
    log.info("db.pre_migration_snapshot", path=str(dest))
    return dest


def run_migrations(conn: Connection, from_version: int | None) -> None:
    """Apply every migration newer than *from_version*; stamp the new version.

    ``from_version=None`` means a fresh database: ``create_all`` just built the
    current schema, so there is nothing to apply — only stamp it.
    """
    if from_version is not None:
        for version, description, fn in MIGRATIONS:
            if version > from_version:
                log.info("db.migrating", version=version, description=description)
                fn(conn)
    conn.exec_driver_sql(f"PRAGMA user_version = {LATEST_VERSION}")
