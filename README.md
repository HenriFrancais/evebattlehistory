# NV Battle Reports

NV Tools integration app for killmail battle-report analytics.

---

## Backups (Google Drive via rclone)

### One-time setup

1. Install rclone on the VM (or use the one baked into the Docker image).
2. Authenticate with a dedicated corporate Google account:
   ```
   rclone config
   ```
   Create a new remote named `nvbr-gdrive` (type: `drive`). Target a dedicated
   sub-folder in that account's Drive so NV BR data stays isolated.
3. Copy the resulting `rclone.conf` to `deploy/rclone-config/rclone.conf`
   (the directory is mounted into the backup container so rclone can rewrite
   the file on OAuth token refresh).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `BACKUP_RCLONE_REMOTE` | *(empty — disabled)* | rclone remote path, e.g. `nvbr-gdrive:nvbr/backups` |
| `BACKUP_KEEP` | `30` | Number of daily snapshots to keep |
| `BACKUP_HOUR` | `3` | UTC hour to run the daily backup |
| `RESTORE_ON_START` | `false` | Pull latest snapshot on startup if DB is absent |

Set these in `deploy/.env`.

### Running with backups

Base deployment (no rclone required):
```
cd deploy && docker compose up -d
```

With the backup sidecar (requires `rclone-config/rclone.conf`):
```
cd deploy && docker compose --profile backup up -d
```

The `--profile backup` flag is the only difference. The app service itself
never calls rclone — only the sidecar does.

### How it works

- **Backup**: `python -m app.backup` takes a WAL-consistent SQLite snapshot
  (online-backup API — no lock on the live DB), copies logs, and pushes to
  `<BACKUP_RCLONE_REMOTE>/<YYYYMMDD-HHMMSS>/`. Older snapshots beyond
  `BACKUP_KEEP` are purged by `rclone purge`.

- **Restore on startup**: If `RESTORE_ON_START=true` and the DB is absent
  (or a `.restored` marker is not present), the app pulls the lexicographically
  latest snapshot from the remote before initialising models. A failed restore
  logs the error and continues with a fresh DB — it never blocks startup.

- **Sidecar**: `scripts/backup-loop.sh` runs inside the `backup` container,
  sleeping until `BACKUP_HOUR:00 UTC` each day, then calling
  `python -m app.backup`. A failed run logs and continues the scheduler.
