#!/usr/bin/env bash
# In-container, self-scheduling Google Drive backup loop for the `backup` compose
# profile. Runs daily at BACKUP_HOUR:00 UTC: takes a WAL-consistent snapshot via
# python -m app.backup into a temp dir, rclone-copies it to the remote, prunes to
# BACKUP_KEEP newest. Logs to stdout so `docker compose logs backup` shows activity.
#
# Disabled mode: if BACKUP_RCLONE_REMOTE is empty, log and `sleep infinity` — the
# service uses `restart: unless-stopped`, and a clean exit would cause a restart loop.
set -uo pipefail

REMOTE="${BACKUP_RCLONE_REMOTE:-}"
KEEP="${BACKUP_KEEP:-30}"
HOUR="${BACKUP_HOUR:-3}"

log() { echo "[backup] $(date -u +'%Y-%m-%d %H:%M:%S UTC') $*"; }

if [ -z "$REMOTE" ]; then
  log "BACKUP_RCLONE_REMOTE is empty; backups disabled. Idling."
  exec sleep infinity
fi

log "starting: remote=$REMOTE keep=$KEEP hour=${HOUR}:00 UTC config=${RCLONE_CONFIG:-<default>}"

run_backup() {
  log "run: invoking python -m app.backup"
  if ! python -m app.backup; then
    log "FAILED: python -m app.backup exited non-zero"
    return 1
  fi
  log "run: complete"
}

while true; do
  # Seconds until the next HOUR:00 UTC. Use today's slot if still ahead; else tomorrow.
  now="$(date -u +%s)"
  target="$(date -u -d "today ${HOUR}:00" +%s)"
  if [ "$target" -le "$now" ]; then
    target="$(date -u -d "tomorrow ${HOUR}:00" +%s)"
  fi
  wait_s=$(( target - now ))
  log "next run in ${wait_s}s (at $(date -u -d "@$target" +'%Y-%m-%d %H:%M:%S') UTC)"
  sleep "$wait_s"

  # One failed run must not kill the scheduler.
  if ! run_backup; then
    log "run failed; continuing scheduler"
  fi
done
