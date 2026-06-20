# NV Battle Reports

Killmail **battle-report analytics** for NV Tools. Ingests zKillboard battle reports
and EVE gamelogs, reconstructs each fight, and renders a fleet-level damage / cap /
EWAR timeline, a fleet-composition breakdown, and per-character drill-downs. The app
runs as an authenticated **iframe inside the NV Tools portal** (`tools.novacancies.space`).

- **Backend** — FastAPI (Python 3.12), SQLAlchemy async over SQLite.
- **Frontend** — React + TypeScript SPA built with Vite; uPlot charts.
- **Auth** — no login of its own; the NV Tools proxy authenticates the user and forwards
  a shared bearer + `X-User-*` identity headers (see [NV Tools integration](#nv-tools-integration)).

---

## Architecture at a glance

```
Browser
  → https://tools.novacancies.space/<ns>/<prefix>/   NV Tools: auth, injects bearer + X-User-* headers
  → https://<your-vm-hostname>/<prefix>/             YOUR VM: Caddy terminates TLS on :443
  → http://127.0.0.1:8000/<prefix>/                  app container (gunicorn+uvicorn), loopback only
```

The FastAPI app (`app/main.py`) mounts every router and the built SPA under a configurable
**URL prefix** (`/br` in production, empty in local dev), enforces the bearer in
`NVToolsAuthMiddleware`, and emits the CSP frame-ancestors header on every response.

### Repository layout

| Path | What |
|---|---|
| `app/` | FastAPI backend — `api/` (routers), `analytics/` (fleet timeline, composition, weapons, sides), `logs/` (gamelog parse/ingest), `killmail/`, `ingest/`, `roster/`, `esi/`, `db/` |
| `frontend/` | Vite SPA — `src/views/` (pages), `src/components/`, `src/api.ts` (typed backend client) |
| `deploy/` | `Dockerfile`, `docker-compose.yml`, `Caddyfile` |
| `config.toml` | Committed app config (who may create BRs, the "us" entity set) |
| `config.local.toml` | Git-ignored local override of `config.toml` |
| `.env` / `.env.example` | Secrets + locations (git-ignored / template) |
| `data_demo/` | Committed fixtures used when `DATA_SOURCE=demo` |
| `var/` | Runtime data (SQLite DB, uploaded logs, ESI/SDE caches) — git-ignored |
| `docs/superpowers/` | Design specs and implementation plans |

---

## Configuration

Two layers:

**1. `.env` → `Settings`** (secrets + per-deploy locations). Copy `.env.example` to `.env`.

| Var | Default | Purpose |
|---|---|---|
| `NV_TOKEN` | `dev-token-change-me` | **Inbound** bearer the NV Tools proxy must present; every non-health request without it gets 401. |
| `URL_PREFIX` | *(empty)* | Path the app mounts under (e.g. `/br`). Empty = served at root (local dev). |
| `DATA_SOURCE` | `real` | `real` = call NV Tools portal + ESI/zKill; `demo` = read `data_demo/` fixtures. |
| `NV_API_URL` | `https://tools.novacancies.space/api` | **Outbound** NV Tools portal API (roster lookup). |
| `NV_API_TOKEN` | *(empty)* | Outbound bearer for `NV_API_URL` — **separate** from `NV_TOKEN`. |
| `DB_PATH` | `./var/db/nvbr.db` | SQLite database file. |
| `LOG_DIR` / `ESI_CACHE_DIR` / `SDE_DIR` | `./var/...` | Uploaded gamelogs, ESI cache, SDE artifacts. |
| `MAX_LOG_MB` | `20` | Per-file gamelog upload cap. |
| `BACKUP_RCLONE_REMOTE` / `BACKUP_KEEP` / `BACKUP_HOUR` / `RESTORE_ON_START` | see [Backups](#backups) | Daily rclone → Google Drive backups. |
| `DEV_MODE` | `false` | **Local only.** Bypasses the bearer check and injects a synthetic user. Never enable in production. |
| `DEV_USER_RANK` / `DEV_USER_TEAMS` | *(empty)* | The synthetic user's rank/teams when `DEV_MODE=true` (e.g. `High Command` / `fc` to act as an FC). |
| `LOG_LEVEL` | `INFO` | Structured-log level. |

**2. `config.toml` → `AppConfig`** (fleet data; `config.local.toml` overrides it when present):

```toml
create_ranks = ["CEO", "Director", "High Command"]   # who may CREATE a battle report …
create_teams = ["fc"]                                # … OR any of these teams (case-insensitive)
our_alliance_ids = [99006113, 99009324, 99014963]    # the "us" set for win/loss + side labelling
our_corp_ids = []
```

The three NV blue alliances (`99006113` No Vacancies., `99009324` Wardec Mechanics,
`99014963` Intended Behavior) are an always-on baseline merged in by `get_app_config()`,
so side labelling works even if the config file omits them.

---

## Running locally

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/) (Python 3.12) and Node 22+.

```bash
# 1. Backend deps
uv sync

# 2. Frontend deps
cd frontend && npm install && cd ..

# 3. Config — copy the template and set DEV_MODE so you don't need a real bearer
cp .env.example .env
```

For a local session, set these in `.env`:

```ini
DEV_MODE=true
DEV_USER_RANK=High Command   # makes you an elevated (FC/HC) user
DEV_USER_TEAMS=fc
DATA_SOURCE=demo             # or real, with NV_API_TOKEN set, to hit the live portal
```

Run the two dev servers in separate terminals:

```bash
# Backend on :8000  (the .env values above are picked up automatically)
uv run uvicorn app.main:app --port 8000

# Frontend on :5173 — Vite proxies /api and /healthz to :8000
cd frontend && npm run dev
```

Open **http://localhost:5173/**. The browser talks to Vite, which proxies API calls to the
backend. With `DEV_MODE=true` you are auto-authenticated as the synthetic user, so no bearer
or NV Tools proxy is needed.

> **Real data locally:** set `DATA_SOURCE=real` and a valid `NV_API_TOKEN`; the app will call
> the live NV Tools roster API and resolve names via ESI. Point `DB_PATH` at a populated SQLite
> file (e.g. `./var/db/dev.db`) to browse already-ingested battle reports.

### Tests, types, lint

```bash
uv run pytest                       # backend
uv run ruff check app tests         # backend lint
uv run mypy app                     # backend types
cd frontend && npm test             # frontend (vitest)
cd frontend && npx tsc --noEmit     # frontend types
cd frontend && npm run build        # production SPA build
```

---

## Containerised deployment (VM)

The app ships as a single multistage image: stage 1 builds the SPA, stage 2 is a Python
runtime serving the API **and** the built SPA via gunicorn (uvicorn workers), bound to
`127.0.0.1:8000` and running as a non-root user. A Caddy reverse proxy on the VM terminates
TLS and forwards to that loopback port.

### 1. Configure

```bash
cd deploy
cp ../.env.example .env          # then edit:
```

Set in `deploy/.env` for production:

```ini
NV_TOKEN=<shared secret the NV Tools admin gives you>   # MUST match exactly
URL_PREFIX=/br                                           # the path NV Tools forwards (confirm with admin!)
DATA_SOURCE=real
NV_API_TOKEN=<outbound portal bearer>
DEV_MODE=false                                           # never true in prod
RESTORE_ON_START=true                                    # optional: pull latest backup if DB absent
```

> **URL-prefix gotcha:** `URL_PREFIX` must equal the exact path NV Tools forwards to your
> upstream. Ask the admin: *"for public `/<ns>/<prefix>/`, what path do you send to my
> upstream?"* The compose file passes `URL_PREFIX` to the image as the `VITE_URL_PREFIX`
> build arg so the SPA's asset paths match — change it and you must rebuild. Caddy must
> **not** strip the prefix.

### 2. Build & run

```bash
cd deploy
docker compose up -d --build           # app only
# or, with the daily backup sidecar (needs ./rclone-config/rclone.conf):
docker compose --profile backup up -d --build
```

`docker-compose.yml` binds `127.0.0.1:8000:8000`, mounts a named `nvbr_data` volume at
`/app/var` (SQLite DB + logs persist across restarts), and adds a healthcheck on
`${URL_PREFIX}/healthz`.

### 3. TLS reverse proxy (Caddy)

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
# edit: replace your-nvbr-hostname.example with the hostname the admin assigns
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy auto-provisions a Let's Encrypt cert once DNS for the hostname points at the VM. It
forwards to `127.0.0.1:8000` and leaves the prefix + `Authorization`/`X-User-*` headers
untouched (default `reverse_proxy` behaviour — do **not** use `handle_path`). The container
binds loopback only; the sole inbound ports open on the VM should be `443` and SSH.

### 4. Hand off to the NV Tools admin

Give them the VM's **public IP** and hostname. They set DNS, point NV Tools at your
`https://<hostname>` upstream, and confirm the bearer matches `NV_TOKEN`.

### 5. Verify (in order)

```bash
# App enforces auth on loopback (health is open; /api/* needs the bearer)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/br/healthz   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/br/api/me    # 401

# Through Caddy over TLS
curl -is https://<hostname>/br/api/me | head -1                             # 401 → Caddy reaches the app

# With the bearer + identity headers → 200 and the CSP header present
curl -is -H "Authorization: Bearer <NV_TOKEN>" -H "X-User-Name: You" \
     -H "X-User-Rank: CEO" https://<hostname>/br/api/me \
  | grep -iE "HTTP/|content-security-policy"
```

Then load it through the NV Tools portal in a browser. A **branded** 401/404 from this app
means the request reached you (a prefix/token question); a **generic** proxy 404 means it
never did (DNS / Caddy / `:443` missing).

### Updating a deployment

```bash
cd deploy && git pull && docker compose up -d --build
```

The SQLite DB and logs live in the `nvbr_data` volume and survive rebuilds.

---

## Backups (rclone → Google Drive)

An optional sidecar takes a daily WAL-consistent SQLite snapshot (online-backup API — no lock
on the live DB), copies uploaded logs, and pushes to a dedicated Google Drive folder.

### One-time setup

1. Install rclone (or use the one baked into the image) and authenticate a **dedicated**
   corporate Google account: `rclone config` → new remote named `nvbr-gdrive` (type `drive`),
   targeting an isolated sub-folder.
2. Copy the resulting config to `deploy/rclone-config/rclone.conf` (mount the **directory**, not
   the file, so rclone can rewrite it on OAuth token refresh).

### Settings (`deploy/.env`)

| Var | Default | Description |
|---|---|---|
| `BACKUP_RCLONE_REMOTE` | *(empty — disabled)* | e.g. `nvbr-gdrive:nvbr/backups` |
| `BACKUP_KEEP` | `30` | Daily snapshots to retain |
| `BACKUP_HOUR` | `3` | UTC hour to run |
| `RESTORE_ON_START` | `false` | On boot, pull the latest snapshot if the DB is absent |

### Run with backups

```bash
cd deploy && docker compose --profile backup up -d
```

- **Backup:** `python -m app.backup` snapshots the DB + logs and pushes to
  `<BACKUP_RCLONE_REMOTE>/<YYYYMMDD-HHMMSS>/`; older snapshots beyond `BACKUP_KEEP` are purged.
- **Restore on startup:** if `RESTORE_ON_START=true` and the DB is absent, the latest snapshot
  is pulled before models initialise. A failed restore logs and continues with a fresh DB —
  it never blocks startup.
- **Sidecar:** `scripts/backup-loop.sh` sleeps until `BACKUP_HOUR:00 UTC` daily, then runs the
  backup; a failed run logs and continues.

---

## NV Tools integration

The app is a normal service behind the NV Tools authenticating proxy. The contract
(`app/middleware.py`):

- **Bearer is the whole trust model.** Every non-health request without
  `Authorization: Bearer <NV_TOKEN>` is rejected with **401**. Identity is trusted only after
  the bearer passes.
- **Identity headers:** `X-User-Name`, `X-User-Rank`, `X-User-Teams` (comma-separated),
  `X-User-Main-Character-Id` — copied into `request.state` and read via `app/api/auth.py`.
  Elevated actions (creating BRs, the per-character and by-user views) gate on `can_create_br`.
- **CSP for the iframe:** every response carries
  `Content-Security-Policy: frame-ancestors https://tools.novacancies.space https://novacancies.space`.
  No `X-Frame-Options`.
- **Embed script:** `frontend/index.html` includes
  `https://tools.novacancies.space/static/nv_embed.js` once in `<head>` — it mirrors the URL/title
  to the parent frame and re-auths on 401.
- **Stateless:** no cookies/sessions (they don't round-trip through the iframe). Cross-app and
  external links use `target="_top"`.
- **`DEV_MODE=true`** bypasses the bearer and injects a synthetic user for local development —
  it must stay `false` on the VM.
