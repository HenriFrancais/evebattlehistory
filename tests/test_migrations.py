"""Versioned schema migrations run at startup (PRAGMA user_version)."""

from __future__ import annotations

import sqlite3

from app.config import get_settings
from app.db.engine import init_models, reset_engine_for_tests

_DROPPED = [
    ("killmail", "damage_taken"),
    ("battle_report", "discord_thread_id"),
    ("battle_report", "discord_thread_url"),
    ("log_event", "source_name"),
    ("log_event", "authoritative"),
    ("log_event", "dedupe_suppressed"),
]


def _columns(db: str, table: str) -> set[str]:
    with sqlite3.connect(db) as c:
        return {r[1] for r in c.execute(f"pragma table_info({table})")}


def _user_version(db: str) -> int:
    with sqlite3.connect(db) as c:
        return int(c.execute("pragma user_version").fetchone()[0])


async def _boot(monkeypatch, db_file) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DB_PATH", str(db_file))
    get_settings.cache_clear()
    reset_engine_for_tests()
    await init_models(get_settings())
    reset_engine_for_tests()


async def test_fresh_db_is_stamped_at_latest_version(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.migrate import LATEST_VERSION

    db = tmp_path / "fresh.db"
    await _boot(monkeypatch, db)
    assert LATEST_VERSION >= 1
    assert _user_version(str(db)) == LATEST_VERSION
    assert not list(tmp_path.glob("fresh.db.pre-v*"))  # nothing to snapshot


async def test_old_db_gets_missing_columns_and_a_snapshot(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.migrate import LATEST_VERSION

    db = tmp_path / "old.db"
    await _boot(monkeypatch, db)
    # Rewind to a pre-migration production shape: columns that were historically
    # added by hand are absent and the DB is unversioned.
    with sqlite3.connect(db) as c:
        c.execute("drop index if exists ix_log_event_ewar_dedupe")
        for table, col in _DROPPED:
            c.execute(f"alter table {table} drop column {col}")
        c.execute("insert into solar_system (system_id, name) values (1, 'J1')")
        c.execute("pragma user_version = 0")
    assert "damage_taken" not in _columns(str(db), "killmail")

    await _boot(monkeypatch, db)

    for table, col in _DROPPED:
        assert col in _columns(str(db), table), f"{table}.{col} not migrated"
    assert _user_version(str(db)) == LATEST_VERSION
    snaps = list(tmp_path.glob("old.db.pre-v*"))
    assert len(snaps) == 1
    with sqlite3.connect(snaps[0]) as c:  # snapshot is the PRE-migration data
        assert c.execute("select name from solar_system").fetchone()[0] == "J1"
        assert "damage_taken" not in {r[1] for r in c.execute("pragma table_info(killmail)")}


async def test_migrations_are_idempotent(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.migrate import LATEST_VERSION

    db = tmp_path / "twice.db"
    await _boot(monkeypatch, db)
    await _boot(monkeypatch, db)
    assert _user_version(str(db)) == LATEST_VERSION
    assert not list(tmp_path.glob("twice.db.pre-v*"))
