"""Backup + restore logic for the NV Battle Reports app.

Uses the SQLite online-backup API for WAL-consistent snapshots (no live lock).
Shells out to rclone for cloud storage.

Remote layout::

    <remote>/<YYYYMMDD-HHMMSS>/app.db   one DB snapshot per run (pruned to BACKUP_KEEP)
    <remote>/logs/<sha256>.txt          uploaded logs, stored ONCE (content-addressed,
                                        so an incremental copy never rewrites a file)

A backup that fails is LOUD: ``run_backup`` raises ``BackupError``, never prunes
older snapshots, and the CLI exits non-zero. Restore-on-start stays best-effort
(a failed restore logs and lets the app boot with a fresh DB).

CLI entry points:
    python -m app.backup            →  one backup with the current UTC timestamp
    python -m app.backup --verify   →  restore drill: pull the newest snapshot to a
                                       temp dir and run an integrity check on it
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from app.config import Settings
from app.observability.logging import log

#: Shared, incrementally-synced directory of uploaded logs on the remote.
LOGS_DIRNAME = "logs"
#: A snapshot directory name (UTC ``YYYYMMDD-HHMMSS``); anything else on the remote
#: (e.g. the shared logs directory) is never listed, pruned or restored as a snapshot.
_SNAPSHOT_RE = re.compile(r"^\d{8}-\d{6}$")


class BackupError(RuntimeError):
    """A backup or snapshot verification failed."""


# ---------------------------------------------------------------------------
# make_snapshot
# ---------------------------------------------------------------------------


def make_snapshot(settings: Settings, staging_dir: Path) -> Path:
    """Snapshot the live DB into staging_dir/app.db with the SQLite online-backup
    API (WAL-consistent, no live lock). Logs are synced separately (see run_backup).

    Returns staging_dir. Sync; callers may wrap in asyncio.to_thread.
    """
    snap_db = staging_dir / "app.db"

    # SQLite online-backup: consistent even under concurrent WAL writes.
    with closing(sqlite3.connect(str(settings.db_path))) as src, closing(
        sqlite3.connect(str(snap_db))
    ) as dst:
        src.backup(dst)

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
    to replace the real subprocess in tests.  Non-zero exits are logged and
    reported through the return value (False / []); callers decide what is fatal.
    """

    def __init__(self, runner: _Runner = _default_runner) -> None:
        self._run = runner

    def push(self, local_dir: Path, dest: str) -> bool:
        """rclone copy local_dir → dest.  Returns False (and logs) on failure."""
        cmd = ["rclone", "copy", str(local_dir), dest, "--transfers=4"]
        result = self._run(cmd)
        if result.returncode != 0:
            log.error("rclone.push_failed", dest=dest, stderr=result.stderr)
            return False
        return True

    def check(self, local_dir: Path, dest: str) -> bool:
        """True iff every file in local_dir exists identically at dest."""
        cmd = ["rclone", "check", str(local_dir), dest, "--one-way"]
        result = self._run(cmd)
        if result.returncode != 0:
            log.error("rclone.check_failed", dest=dest, stderr=result.stderr)
            return False
        return True

    def purge(self, path: str) -> bool:
        """rclone purge path.  Returns False (and logs) on failure."""
        result = self._run(["rclone", "purge", path])
        if result.returncode != 0:
            log.error("rclone.purge_failed", path=path, stderr=result.stderr)
            return False
        return True

    def list_snapshots(self, remote: str) -> list[str]:
        """Snapshot directory names under remote, oldest first."""
        return sorted(d for d in self.list_dirs(remote) if _SNAPSHOT_RE.match(d))

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

    def pull(self, remote_subpath: str, local_dir: Path) -> bool:
        """rclone copy remote_subpath → local_dir.  Returns False (and logs) on failure."""
        local_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["rclone", "copy", remote_subpath, str(local_dir)]
        result = self._run(cmd)
        if result.returncode != 0:
            log.error("rclone.pull_failed", src=remote_subpath, stderr=result.stderr)
            return False
        return True

    def prune(self, remote: str, keep: int) -> None:
        """Purge all but the newest ``keep`` SNAPSHOT dirs of remote (lex sort).

        Only timestamp-named directories are candidates, so the shared logs
        directory is never touched. Logs on individual purge failures.
        """
        dirs = self.list_snapshots(remote)
        to_purge = dirs[: max(0, len(dirs) - keep)]
        for old in to_purge:
            path = f"{remote}/{old}"
            if self.purge(path):
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

    Raises:
        BackupError: the snapshot, push or post-push verification failed. Older
        snapshots are NOT pruned in that case, and the partial directory of the
        failed run is removed so it can never count toward ``backup_keep``.
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
            raise BackupError(f"snapshot failed: {exc}") from exc

        if not (client.push(tmp, dest) and client.check(tmp, dest)):
            client.purge(dest)
            raise BackupError(f"push to {dest} failed or did not verify")

    # Uploaded logs are content-addressed and immutable: sync them once into a
    # shared directory instead of re-copying the whole set into every snapshot.
    if settings.log_dir.exists() and not client.push(
        settings.log_dir, f"{remote}/{LOGS_DIRNAME}"
    ):
        raise BackupError(f"log sync to {remote}/{LOGS_DIRNAME} failed")

    client.prune(remote, settings.backup_keep)
    log.info("backup.complete", remote_path=dest)
    return dest


def verify_latest_snapshot(
    settings: Settings,
    *,
    client: RcloneClient | None = None,
) -> str:
    """Restore drill: pull the newest snapshot to a temp dir and check it opens,
    passes ``PRAGMA integrity_check`` and contains tables. Returns its remote path.

    Raises BackupError when there is no snapshot or it is not a healthy database.
    """
    remote = settings.backup_rclone_remote
    if not remote:
        raise BackupError("backup_rclone_remote is empty")
    if client is None:
        client = RcloneClient()
    snaps = client.list_snapshots(remote)
    if not snaps:
        raise BackupError(f"no snapshots under {remote}")
    remote_snap = f"{remote}/{snaps[-1]}"
    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)
        if not client.pull(f"{remote_snap}/app.db", tmp) or not (tmp / "app.db").exists():
            raise BackupError(f"could not pull {remote_snap}/app.db")
        try:
            with closing(sqlite3.connect(str(tmp / "app.db"))) as con:
                status = con.execute("PRAGMA integrity_check").fetchone()[0]
                tables = con.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"{remote_snap}/app.db is not a database: {exc}") from exc
    if status != "ok" or tables == 0:
        raise BackupError(f"{remote_snap}/app.db failed verification: {status}")
    log.info("backup.verified", remote_path=remote_snap, tables=tables)
    return remote_snap


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

    dirs = client.list_snapshots(remote)
    if not dirs:
        log.info("restore.skipped", reason="no snapshots on remote")
        return False

    latest = dirs[-1]
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

    # Restore logs (non-fatal if missing on remote): the shared directory, plus
    # the per-snapshot copy written by older versions of this module.
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        client.pull(f"{remote}/{LOGS_DIRNAME}", settings.log_dir)
        if LOGS_DIRNAME in client.list_dirs(remote_snap):
            client.pull(f"{remote_snap}/{LOGS_DIRNAME}", settings.log_dir)
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


def _cli(argv: list[str] | None = None) -> None:
    import datetime

    from app.config import get_settings

    args = sys.argv[1:] if argv is None else argv
    settings = get_settings()
    try:
        if "--verify" in args:
            print(f"Snapshot verified: {verify_latest_snapshot(settings)}")
            return
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        result = run_backup(settings, ts)
    except BackupError as exc:
        log.error("backup.failed", error=str(exc))
        _alert(settings, f"NV Battle Reports backup FAILED: {exc}")
        print(f"Backup FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Backup complete: {result}" if result else "Backups disabled (no remote set).")


def _alert(settings: Settings, text: str) -> None:
    """Best-effort Discord alert (needs DISCORD_BOT_TOKEN + DISCORD_ALERT_CHANNEL_ID)."""
    if not (settings.discord_bot_token and settings.discord_alert_channel_id):
        return
    try:
        import httpx

        httpx.post(
            f"https://discord.com/api/v10/channels/{settings.discord_alert_channel_id}/messages",
            headers={"Authorization": f"Bot {settings.discord_bot_token}"},
            json={"content": text[:1900]},
            timeout=15.0,
        )
    except Exception as exc:  # an alert must never mask the real failure
        log.warning("backup.alert_failed", error=str(exc))


if __name__ == "__main__":
    _cli()
