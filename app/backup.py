"""Backup + restore logic for the NV Battle Reports app.

Uses the SQLite online-backup API for WAL-consistent snapshots (no live lock).
Shells out to rclone for cloud storage. All errors are caught and logged so
backup/restore failures never crash startup or the scheduler.

CLI entry point:
    python -m app.backup   →  runs one backup with the current UTC timestamp
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from app.config import Settings
from app.observability.logging import log

# ---------------------------------------------------------------------------
# make_snapshot
# ---------------------------------------------------------------------------


def make_snapshot(settings: Settings, staging_dir: Path) -> Path:
    """Copy the live DB + log_dir into staging_dir using the SQLite online-backup API.

    - staging_dir/app.db  — WAL-consistent snapshot (no live lock)
    - staging_dir/logs/   — recursive copy of log_dir (tolerates missing/empty)

    Returns staging_dir. Sync; callers may wrap in asyncio.to_thread.
    """
    snap_db = staging_dir / "app.db"
    logs_out = staging_dir / "logs"

    # SQLite online-backup: consistent even under concurrent WAL writes.
    with closing(sqlite3.connect(str(settings.db_path))) as src, closing(
        sqlite3.connect(str(snap_db))
    ) as dst:
        src.backup(dst)

    # Copy log_dir recursively; tolerate missing or empty.
    if settings.log_dir.exists():
        shutil.copytree(str(settings.log_dir), str(logs_out), dirs_exist_ok=True)
    else:
        logs_out.mkdir(parents=True, exist_ok=True)

    return staging_dir


# ---------------------------------------------------------------------------
# RcloneClient
# ---------------------------------------------------------------------------

# Type alias for the subprocess.run-compatible callable used in tests.
_Runner = Callable[..., Any]


def _default_runner(cmd: list[str], **kwargs: Any) -> Any:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


class RcloneClient:
    """Thin injectable wrapper around the rclone binary.

    Pass a ``runner`` callable (signature: ``(cmd: list[str], **kwargs) -> result``)
    to replace the real subprocess in tests.  Non-zero exits are logged, not raised,
    except for list_dirs which returns [].
    """

    def __init__(self, runner: _Runner = _default_runner) -> None:
        self._run = runner

    def push(self, local_dir: Path, dest: str) -> None:
        """rclone copy local_dir → dest.  Logs on failure, does not raise."""
        cmd = ["rclone", "copy", str(local_dir), dest, "--transfers=4"]
        result = self._run(cmd)
        if result.returncode != 0:
            log.error("rclone.push_failed", dest=dest, stderr=result.stderr)

    def list_dirs(self, remote: str) -> list[str]:
        """Return directory names (without trailing slash) under remote.

        Returns [] on any rclone error.
        """
        cmd = ["rclone", "lsf", remote, "--dirs-only"]
        result = self._run(cmd)
        if result.returncode != 0:
            log.warning("rclone.list_dirs_failed", remote=remote, stderr=result.stderr)
            return []
        # rclone lsf appends a trailing slash to each dir entry
        return [line.rstrip("/") for line in result.stdout.splitlines() if line.strip()]

    def pull(self, remote_subpath: str, local_dir: Path) -> None:
        """rclone copy remote_subpath → local_dir.  Logs on failure, does not raise."""
        local_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["rclone", "copy", remote_subpath, str(local_dir)]
        result = self._run(cmd)
        if result.returncode != 0:
            log.error("rclone.pull_failed", src=remote_subpath, stderr=result.stderr)

    def prune(self, remote: str, keep: int) -> None:
        """Purge all but the newest ``keep`` subdirs of remote (lex sort).

        Logs on individual purge failures, does not raise.
        """
        dirs = sorted(self.list_dirs(remote))
        to_purge = dirs[: max(0, len(dirs) - keep)]
        for old in to_purge:
            path = f"{remote}/{old}"
            cmd = ["rclone", "purge", path]
            result = self._run(cmd)
            if result.returncode != 0:
                log.error("rclone.purge_failed", path=path, stderr=result.stderr)
            else:
                log.info("rclone.pruned", path=path)


# ---------------------------------------------------------------------------
# run_backup
# ---------------------------------------------------------------------------


def run_backup(
    settings: Settings,
    timestamp: str,
    *,
    client: RcloneClient | None = None,
) -> str | None:
    """Take a snapshot and push it to the rclone remote.

    Args:
        settings:  Runtime settings (backup_rclone_remote, backup_keep, …).
        timestamp: Caller-supplied stamp (``YYYYMMDD-HHMMSS``); kept injectable
                   so unit tests are deterministic.
        client:    Optional RcloneClient; defaults to one with the real subprocess.

    Returns:
        The remote path (``<remote>/<timestamp>``) on success, or None when
        backups are disabled (empty remote).
    """
    if not settings.backup_rclone_remote:
        log.info("backup.disabled", reason="backup_rclone_remote is empty")
        return None

    if client is None:
        client = RcloneClient()

    remote = settings.backup_rclone_remote
    dest = f"{remote}/{timestamp}"

    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)
        try:
            make_snapshot(settings, tmp)
        except Exception as exc:
            log.error("backup.snapshot_failed", error=str(exc))
            return None

        client.push(tmp, dest)
        client.prune(remote, settings.backup_keep)

    log.info("backup.complete", remote_path=dest)
    return dest


# ---------------------------------------------------------------------------
# restore_if_empty
# ---------------------------------------------------------------------------


def restore_if_empty(
    settings: Settings,
    *,
    client: RcloneClient | None = None,
) -> bool:
    """Pull the latest snapshot from the remote if the local DB is absent/empty.

    Returns True when a restore was performed, False for any no-op path.
    Errors are caught, logged, and return False — never blocks startup.
    """
    try:
        return _restore_if_empty_inner(settings, client=client)
    except Exception as exc:
        log.error("restore.unexpected_error", error=str(exc))
        return False


def _restore_if_empty_inner(
    settings: Settings,
    *,
    client: RcloneClient | None = None,
) -> bool:
    if not settings.restore_on_start:
        log.debug("restore.skipped", reason="restore_on_start=False")
        return False

    marker = settings.db_path.parent / ".restored"
    if marker.exists():
        log.info("restore.skipped", reason=".restored marker present")
        return False

    # DB already present and non-empty → no need to restore.
    if settings.db_path.exists() and settings.db_path.stat().st_size > 0:
        log.info("restore.skipped", reason="db_path already non-empty")
        return False

    if client is None:
        client = RcloneClient()

    remote = settings.backup_rclone_remote
    if not remote:
        log.info("restore.skipped", reason="backup_rclone_remote is empty")
        return False

    dirs = client.list_dirs(remote)
    if not dirs:
        log.info("restore.skipped", reason="no snapshots on remote")
        return False

    latest = sorted(dirs)[-1]
    remote_snap = f"{remote}/{latest}"

    # Restore app.db — pull the snapshot file then rename to the configured db name.
    db_dir = settings.db_path.parent
    db_dir.mkdir(parents=True, exist_ok=True)
    client.pull(f"{remote_snap}/app.db", db_dir)

    pulled_db = db_dir / "app.db"
    if not pulled_db.exists():
        log.error("restore.db_missing_after_pull", remote_snap=remote_snap)
        return False

    # Rename to the configured db filename if it differs from "app.db".
    if pulled_db != settings.db_path:
        pulled_db.rename(settings.db_path)

    # Restore logs (non-fatal if missing on remote)
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        client.pull(f"{remote_snap}/logs", settings.log_dir)
    except Exception as exc:
        log.warning("restore.logs_failed", error=str(exc))

    if not settings.db_path.exists():
        log.error("restore.db_missing_after_rename", remote_snap=remote_snap)
        return False

    marker.write_text(f"restored from {remote_snap}\n")
    log.info("restore.complete", remote_snap=remote_snap, db_path=str(settings.db_path))
    return True


# ---------------------------------------------------------------------------
# CLI entry point: python -m app.backup
# ---------------------------------------------------------------------------


def _cli() -> None:
    import datetime

    settings_mod = __import__("app.config", fromlist=["get_settings"])
    settings = settings_mod.get_settings()
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    result = run_backup(settings, ts)
    if result:
        print(f"Backup complete: {result}")
    else:
        print("Backup disabled or failed — check logs.")


if __name__ == "__main__":
    _cli()
