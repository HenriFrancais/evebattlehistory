#!/usr/bin/env bash
#
# Deploy / update NV Battle Reports on the VM.
#
#   ./deploy/deploy.sh [--reparse] [--skip-backfill] [--no-pull]
#
# Default: pull latest code, rebuild the image (incl. the SPA) and restart the
# container, wait until it's healthy, then run the off-BR counterparty backfill.
# The new `br_char_ship` table is created automatically on startup.
#
# Flags:
#   --reparse         also re-parse all stored gamelogs first (one-time, after the
#                     custom-ship-name fix; cleans stored names so the backfill
#                     matches more counterparties). Slow on large datasets.
#   --skip-backfill   don't run the ESI counterparty backfill (code-only deploy).
#   --no-pull         don't run `git pull` (deploy whatever is checked out).
#
# First deploy of the log-identified-participants feature:
#   ./deploy/deploy.sh --reparse
# Routine code deploy afterwards:
#   ./deploy/deploy.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE=nvbr

PULL=1
REPARSE=0
BACKFILL=1
for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --reparse) REPARSE=1 ;;
    --skip-backfill) BACKFILL=0 ;;
    -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# Compose v2 (`docker compose`) preferred; fall back to v1 (`docker-compose`).
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "error: neither 'docker compose' nor 'docker-compose' is available" >&2
  exit 1
fi

# Run compose from deploy/ so .env interpolation + relative paths match manual use.
cd "$SCRIPT_DIR"

if [ "$PULL" = 1 ]; then
  echo "==> git pull --ff-only"
  git -C "$REPO_DIR" pull --ff-only
fi

echo "==> build + restart ($SERVICE)"
$DC up -d --build

echo "==> waiting for $SERVICE to become healthy"
ready=0
for _ in $(seq 1 30); do
  if $DC exec -T "$SERVICE" python -c \
      "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:8000'+os.environ.get('URL_PREFIX','')+'/healthz')" \
      >/dev/null 2>&1; then
    ready=1; echo "    ready"; break
  fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "!! $SERVICE did not become healthy — check: $DC logs $SERVICE" >&2
  exit 1
fi

if [ "$REPARSE" = 1 ]; then
  echo "==> re-parsing gamelogs (cleans stored names)"
  $DC exec -T "$SERVICE" python -m app.logs.reparse
fi

if [ "$BACKFILL" = 1 ]; then
  echo "==> backfilling off-BR counterparty characters via ESI"
  $DC exec -T "$SERVICE" python -m app.fights.offbr_resolve
fi

echo "==> deploy complete"
