"""Tests for app/backup.py.

All backup/restore logic tests use a real local-path rclone remote in a tmp
dir — genuine round-trips, no Google auth or network required.
Rclone tests are skipped if rclone is not on PATH.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.config import Settings

# ---------------------------------------------------------------------------
# Helpers / skip guard
# ---------------------------------------------------------------------------

RCLONE_AVAILABLE = shutil.which("rclone") is not None

requires_rclone = pytest.mark.skipif(
    not RCLONE_AVAILABLE,
    reason="rclone not found on PATH",
)


def _make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Return a Settings with db_path + log_dir inside tmp_path."""
    db_path = tmp_path / "var" / "db" / "nvbr.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = tmp_path / "var" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        nv_token="test",
        db_path=db_path,
        log_dir=log_dir,
        **overrides,  # type: ignore[arg-type]
    )


def _seed_db(db_path: Path) -> None:
    """Write a minimal SQLite database so the file is non-empty with a table."""
    with closing(sqlite3.connect(str(db_path))) as con:
        con.execute("CREATE TABLE IF NOT EXISTS sentinel (id INTEGER PRIMARY KEY, val TEXT)")
        con.execute("INSERT INTO sentinel VALUES (1, 'hello')")
        con.commit()


def _rows(db_path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(str(db_path))) as con:
        return con.execute("SELECT * FROM sentinel").fetchall()


# ---------------------------------------------------------------------------
# make_snapshot
# ---------------------------------------------------------------------------


def test_make_snapshot_copies_db(tmp_path: Path) -> None:
    """make_snapshot produces app.db in staging_dir; rows are intact."""
    from app.backup import make_snapshot

    settings = _make_settings(tmp_path)
    _seed_db(settings.db_path)
    # Add a log file
    (settings.log_dir / "app.log").write_text("some log content")

    staging = tmp_path / "stage"
    staging.mkdir()
    result = make_snapshot(settings, staging)

    snap_db = staging / "app.db"
    assert snap_db.exists(), "app.db should be in staging_dir"
    assert _rows(snap_db) == [(1, "hello")], "rows should be copied faithfully"
    assert result == staging


def test_make_snapshot_copies_logs(tmp_path: Path) -> None:
    """make_snapshot copies log_dir contents into staging_dir/logs/."""
    from app.backup import make_snapshot

    settings = _make_settings(tmp_path)
    _seed_db(settings.db_path)
    (settings.log_dir / "app.log").write_text("line one")
    (settings.log_dir / "app.log.1").write_text("line two")

    staging = tmp_path / "stage"
    staging.mkdir()
    make_snapshot(settings, staging)

    assert (staging / "logs" / "app.log").read_text() == "line one"
    assert (staging / "logs" / "app.log.1").read_text() == "line two"


def test_make_snapshot_tolerates_missing_log_dir(tmp_path: Path) -> None:
    """make_snapshot does not raise when log_dir doesn't exist."""
    from app.backup import make_snapshot

    settings = _make_settings(tmp_path)
    _seed_db(settings.db_path)
    shutil.rmtree(settings.log_dir)  # remove it

    staging = tmp_path / "stage"
    staging.mkdir()
    result = make_snapshot(settings, staging)  # must not raise
    assert result == staging
    # logs/ might not exist or might be empty — both are fine
    logs_dir = staging / "logs"
    if logs_dir.exists():
        assert list(logs_dir.iterdir()) == []


def test_make_snapshot_tolerates_empty_log_dir(tmp_path: Path) -> None:
    """make_snapshot does not raise when log_dir exists but is empty."""
    from app.backup import make_snapshot

    settings = _make_settings(tmp_path)
    _seed_db(settings.db_path)
    # log_dir exists but is empty (created by _make_settings)

    staging = tmp_path / "stage"
    staging.mkdir()
    result = make_snapshot(settings, staging)
    assert result == staging


# ---------------------------------------------------------------------------
# run_backup: disabled (empty remote)
# ---------------------------------------------------------------------------


def test_run_backup_disabled_returns_none(tmp_path: Path) -> None:
    """run_backup returns None and creates nothing when remote is empty."""
    from app.backup import run_backup

    settings = _make_settings(tmp_path, backup_rclone_remote="")
    result = run_backup(settings, "20240101-120000")
    assert result is None


# ---------------------------------------------------------------------------
# run_backup: real rclone round-trip
# ---------------------------------------------------------------------------


@requires_rclone
def test_run_backup_roundtrip(tmp_path: Path) -> None:
    """run_backup uploads to a local-path remote; snapshot file exists."""
    from app.backup import run_backup

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    settings = _make_settings(
        tmp_path,
        backup_rclone_remote=str(remote_dir),
        backup_keep=30,
    )
    _seed_db(settings.db_path)
    (settings.log_dir / "app.log").write_text("data")

    ts = "20240101-120000"
    result = run_backup(settings, ts)

    assert result is not None
    assert (remote_dir / ts / "app.db").exists(), "app.db should be at remote/<ts>/app.db"


@requires_rclone
def test_run_backup_prune(tmp_path: Path) -> None:
    """run_backup with backup_keep=1 prunes older snapshots, keeps newest."""
    from app.backup import run_backup

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    settings = _make_settings(
        tmp_path,
        backup_rclone_remote=str(remote_dir),
        backup_keep=1,
    )
    _seed_db(settings.db_path)

    ts1 = "20240101-120000"
    ts2 = "20240102-120000"
    run_backup(settings, ts1)
    run_backup(settings, ts2)

    dirs = [d.name for d in remote_dir.iterdir() if d.is_dir()]
    assert ts2 in dirs, "newest should survive"
    assert ts1 not in dirs, "oldest should be pruned"


# ---------------------------------------------------------------------------
# restore_if_empty
# ---------------------------------------------------------------------------


@requires_rclone
def test_restore_if_empty_pulls_db(tmp_path: Path) -> None:
    """restore_if_empty restores DB + logs from a local-path remote."""
    from app.backup import restore_if_empty, run_backup

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()

    # Build source settings (has a populated DB)
    src_settings = _make_settings(tmp_path / "src")
    _seed_db(src_settings.db_path)
    (src_settings.log_dir / "app.log").write_text("some log")

    # Run a backup so remote has data
    run_backup(
        Settings(
            nv_token="test",
            db_path=src_settings.db_path,
            log_dir=src_settings.log_dir,
            backup_rclone_remote=str(remote_dir),
            backup_keep=30,
        ),
        "20240101-120000",
    )

    # Fresh (empty) settings — db doesn't exist yet
    dst_settings = _make_settings(
        tmp_path / "dst",
        backup_rclone_remote=str(remote_dir),
        restore_on_start=True,
    )
    # db_path doesn't exist yet (fresh deployment)
    dst_settings.db_path.unlink(missing_ok=True)

    restored = restore_if_empty(dst_settings)

    assert restored is True
    assert dst_settings.db_path.exists(), "DB should be restored"
    assert _rows(dst_settings.db_path) == [(1, "hello")], "rows should be intact"
    # marker should be written
    marker = dst_settings.db_path.parent / ".restored"
    assert marker.exists(), ".restored marker should be created"


@requires_rclone
def test_restore_if_empty_noop_if_marker(tmp_path: Path) -> None:
    """Second call to restore_if_empty is a no-op when marker exists."""
    from app.backup import restore_if_empty, run_backup

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    settings = _make_settings(
        tmp_path,
        backup_rclone_remote=str(remote_dir),
        restore_on_start=True,
    )
    _seed_db(settings.db_path)
    run_backup(
        Settings(
            nv_token="test",
            db_path=settings.db_path,
            log_dir=settings.log_dir,
            backup_rclone_remote=str(remote_dir),
            backup_keep=30,
        ),
        "20240101-120000",
    )

    # Write the marker manually (simulates a previous restore)
    marker = settings.db_path.parent / ".restored"
    marker.write_text("done")

    result = restore_if_empty(settings)
    assert result is False


def test_restore_if_empty_noop_if_restore_on_start_false(tmp_path: Path) -> None:
    """restore_if_empty is a no-op when restore_on_start=False."""
    from app.backup import restore_if_empty

    settings = _make_settings(
        tmp_path,
        backup_rclone_remote="someremote:bucket",
        restore_on_start=False,
    )
    result = restore_if_empty(settings)
    assert result is False


def test_restore_if_empty_noop_if_db_nonempty(tmp_path: Path) -> None:
    """restore_if_empty is a no-op when the DB already has content."""
    from app.backup import restore_if_empty

    settings = _make_settings(
        tmp_path,
        backup_rclone_remote="someremote:bucket",
        restore_on_start=True,
    )
    _seed_db(settings.db_path)  # non-empty DB

    result = restore_if_empty(settings)
    assert result is False


@requires_rclone
def test_restore_if_empty_noop_if_remote_empty(tmp_path: Path) -> None:
    """restore_if_empty is a no-op when remote has no snapshots."""
    from app.backup import restore_if_empty

    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    settings = _make_settings(
        tmp_path,
        backup_rclone_remote=str(remote_dir),
        restore_on_start=True,
    )
    settings.db_path.unlink(missing_ok=True)

    result = restore_if_empty(settings)
    assert result is False


@requires_rclone
def test_restore_if_empty_tolerates_rclone_failure(tmp_path: Path) -> None:
    """restore_if_empty returns False (not crash) on rclone errors."""
    from app.backup import restore_if_empty

    settings = _make_settings(
        tmp_path,
        backup_rclone_remote="/nonexistent/path/that/cannot/exist/ever",
        restore_on_start=True,
    )
    settings.db_path.unlink(missing_ok=True)

    # Should log and return False, never raise
    result = restore_if_empty(settings)
    assert result is False


# ---------------------------------------------------------------------------
# RcloneClient unit tests (fake runner — no subprocess)
# ---------------------------------------------------------------------------


def test_rclone_client_list_dirs_parses_output(tmp_path: Path) -> None:
    """RcloneClient.list_dirs parses rclone lsf --dirs-only output."""
    from app.backup import RcloneClient

    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], **_: object) -> object:
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = "20240101-120000/\n20240102-120000/\n"
            stderr = ""

        return _R()

    client = RcloneClient(runner=fake_runner)
    dirs = client.list_dirs("myremote:bucket")
    assert dirs == ["20240101-120000", "20240102-120000"]
    assert any("lsf" in c for c in calls[0])


def test_rclone_client_list_dirs_returns_empty_on_error(tmp_path: Path) -> None:
    """RcloneClient.list_dirs returns [] on non-zero rclone exit."""
    from app.backup import RcloneClient

    def fake_runner(cmd: list[str], **_: object) -> object:
        class _R:
            returncode = 1
            stdout = ""
            stderr = "error"

        return _R()

    client = RcloneClient(runner=fake_runner)
    dirs = client.list_dirs("myremote:bucket")
    assert dirs == []


def test_rclone_client_prune_keeps_newest(tmp_path: Path) -> None:
    """RcloneClient.prune keeps newest `keep` dirs and purges the rest."""
    from app.backup import RcloneClient

    purged: list[str] = []

    def fake_runner(cmd: list[str], **_: object) -> object:
        if "lsf" in cmd:

            class _R:
                returncode = 0
                stdout = "20240101-120000/\n20240102-120000/\n20240103-120000/\n"
                stderr = ""

            return _R()
        if "purge" in cmd:
            purged.append(cmd[-1])  # last arg is the remote path

        class _R2:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R2()

    client = RcloneClient(runner=fake_runner)
    client.prune("myremote:bucket", keep=2)

    # 3 dirs, keep 2 newest → 1 purged (the oldest)
    assert len(purged) == 1
    assert "20240101-120000" in purged[0]


def test_rclone_client_prune_noop_when_under_keep(tmp_path: Path) -> None:
    """RcloneClient.prune does nothing when dirs count <= keep."""
    from app.backup import RcloneClient

    purged: list[str] = []

    def fake_runner(cmd: list[str], **_: object) -> object:
        if "lsf" in cmd:

            class _R:
                returncode = 0
                stdout = "20240101-120000/\n"
                stderr = ""

            return _R()
        purged.append("purge-called")

        class _R2:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R2()

    client = RcloneClient(runner=fake_runner)
    client.prune("myremote:bucket", keep=5)
    assert purged == []
