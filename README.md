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
**URL prefix** (`/fc/br` in production, empty in local dev), enforces the bearer in
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
| `URL_PREFIX` | *(empty)* | Path the app mounts under (e.g. `/fc/br`). Empty = served at root (local dev). |
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

> **Before you build, confirm the URL prefix with the NV Tools admin.** Ask: *"for public
> `/<ns>/<prefix>/`, what exact path do you forward to my upstream?"* The default here is
> **`/fc/br`**. The compose file bakes `URL_PREFIX` into the SPA at build time (the
> `VITE_URL_PREFIX` build arg sets the asset base path), so changing it later means a
> **rebuild**, not just a restart. Caddy must pass the prefix through unchanged.

### Step 0 — Prerequisites on the VM

- A Linux VM you control, with a DNS hostname the NV Tools admin will point at it.
- **Docker** + the **Docker Compose plugin** (`docker compose version`).
- **Caddy** installed as a system service (`systemctl status caddy`).
- Inbound firewall: open **443** and **SSH only**. The app container never listens publicly.
- Outbound HTTPS to `tools.novacancies.space` (portal roster API), ESI, and zKillboard.

### Step 1 — Get the code

```bash
git clone https://github.com/HenriFrancais/evebattlehistory.git
cd evebattlehistory
```

### Step 2 — Configure secrets & settings

```bash
cd deploy
cp ../.env.example .env
nano .env            # fill in the values below
```

Set in `deploy/.env` for production:

```ini
NV_TOKEN=<shared secret the NV Tools admin gives you>   # INBOUND bearer — must match EXACTLY
URL_PREFIX=/fc/br                                        # the path NV Tools forwards (confirm with admin!)
DATA_SOURCE=real
NV_API_URL=https://tools.novacancies.space/api          # portal API base (default is fine)
NV_API_TOKEN=<outbound portal bearer>                   # OUTBOUND bearer for roster lookups (separate value)
DEV_MODE=false                                          # NEVER true in production (it bypasses auth)
RESTORE_ON_START=true                                   # optional: pull latest backup if the DB is absent
# Backups (optional) — see the Backups section below:
BACKUP_RCLONE_REMOTE=                                   # e.g. nvbr-gdrive:nvbr/backups (empty = disabled)
BACKUP_KEEP=30
BACKUP_HOUR=3
```

There are **two distinct tokens**: `NV_TOKEN` is the *inbound* bearer NV Tools presents to you;
`NV_API_TOKEN` is the *outbound* bearer you present to the portal API. They are different values.

### Step 3 — Build & run

```bash
# from deploy/
docker compose up -d --build           # app only
# or, with the daily Google-Drive backup sidecar (see Backups — needs ./rclone-config/rclone.conf):
docker compose --profile backup up -d --build
```

`docker-compose.yml` binds `127.0.0.1:8000:8000` (loopback only), mounts a named `nvbr_data`
volume at `/app/var` (SQLite DB + uploaded logs persist across rebuilds), and healthchecks
`${URL_PREFIX}/healthz`. The image builds the SPA and bakes the processed CCP SDE at build time.

Check it came up:

```bash
docker compose ps                                  # nvbr should be "healthy"
docker compose logs -f nvbr                        # watch startup
```

### Step 4 — TLS reverse proxy (Caddy)

The VM **must** terminate TLS and forward to the loopback container — NV Tools only runs the
*auth* proxy; it still needs a reachable `https://<hostname>` upstream on your VM.

Edit `deploy/Caddyfile` and set your hostname, then install it:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile     # replace your-nvbr-hostname.example with the real hostname
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Dedicated hostname (simplest) — passes every path through to the app:

```caddyfile
br.your-vm-hostname.example {
	reverse_proxy http://127.0.0.1:8000
}
```

Sharing one hostname with other NV apps — match the prefix (do **not** use `handle_path`, which
strips the prefix the app needs):

```caddyfile
your-vm-hostname.example {
	@nvbr path /fc/br /fc/br/*
	reverse_proxy @nvbr 127.0.0.1:8000
	# ... other apps' matchers ...
}
```

Caddy auto-provisions a Let's Encrypt cert once DNS for the hostname points at the VM, and leaves
the prefix + `Authorization`/`X-User-*` headers untouched (default `reverse_proxy` behaviour).

### Step 5 — Hand off to the NV Tools admin

Give them the VM's **public IP** and hostname. They set DNS, point NV Tools at your
`https://<hostname>` upstream under the agreed prefix, and confirm the bearer matches `NV_TOKEN`.

### Step 6 — Verify (in order)

```bash
# App enforces auth on loopback (health is open; /api/* needs the bearer)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/fc/br/healthz   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/fc/br/api/me    # 401

# Through Caddy over TLS
curl -is https://<hostname>/fc/br/api/me | head -1                             # 401 → Caddy reaches the app

# With the bearer + identity headers → 200 and the CSP header present
curl -is -H "Authorization: Bearer <NV_TOKEN>" -H "X-User-Name: You" \
     -H "X-User-Rank: CEO" https://<hostname>/fc/br/api/me \
  | grep -iE "HTTP/|content-security-policy"
```

Then load it through the NV Tools portal in a browser. A **branded** 401/404 from this app
means the request reached you (a prefix/token question); a **generic** proxy 404 means it
never did (DNS / Caddy / `:443` missing).

### Updating a deployment

```bash
cd evebattlehistory && git pull
cd deploy && docker compose up -d --build
```

The SQLite DB and uploaded logs live in the `nvbr_data` volume and survive rebuilds. Because raw
logs are retained, parser improvements can be applied retroactively:

```bash
docker compose exec nvbr python -m app.logs.reparse     # re-parse all stored logs in place
```

---

## Backups (rclone → Google Drive)

An optional sidecar takes a daily WAL-consistent SQLite snapshot (online-backup API — no lock on
the live DB), copies the uploaded logs alongside it, and pushes both to a dedicated Google Drive
folder via rclone. The DB **and** the raw logs are in every snapshot, so a restore can re-parse
everything if the parser changes.

### Step 1 — Create a dedicated Google account & folder

Use a **dedicated corporate Google account** (not a personal one) so retention only ever touches
these snapshots. In its Google Drive, create an isolated folder, e.g. `nvbr/backups`.

### Step 2 — Authenticate rclone (headless VM)

A VM has no browser, so do the OAuth dance on a **desktop with a browser**, then copy the token
to the VM. (Full detail: <https://rclone.org/remote_setup/>.)

**On your laptop/desktop** (with rclone installed):

```bash
rclone authorize "drive"
# A browser opens → log in as the dedicated account → approve.
# rclone prints a JSON token blob. Copy it.
```

**On the VM**, run the interactive config and paste that token when asked:

```bash
rclone config
#  n) New remote
#  name> nvbr-gdrive
#  Storage> drive
#  client_id / client_secret> (blank is fine)
#  scope> 1   (Full access)  — or "drive.file" to scope to rclone-created files only
#  Edit advanced config? > n
#  Use auto config? > n          ← important on a headless box
#  config_token> <paste the JSON token from rclone authorize>
#  Configure this as a Shared Drive? > n
#  y) Yes this is OK
#  q) Quit config
```

Verify the remote works and can see the folder:

```bash
rclone lsd nvbr-gdrive:nvbr      # should list the "backups" folder (create it if missing)
rclone mkdir nvbr-gdrive:nvbr/backups
```

### Step 3 — Place the rclone config where the sidecar can read it

```bash
mkdir -p deploy/rclone-config
cp ~/.config/rclone/rclone.conf deploy/rclone-config/rclone.conf
```

> Mount the **directory** (`deploy/rclone-config/`), never the single file — rclone rewrites
> `rclone.conf` in place when it refreshes the OAuth token, which fails on a bind-mounted file.
> The compose `backup` service already mounts the directory and runs as root so it can read it.

### Step 4 — Configure backup settings in `deploy/.env`

| Var | Default | Description |
|---|---|---|
| `BACKUP_RCLONE_REMOTE` | *(empty — disabled)* | Destination, e.g. `nvbr-gdrive:nvbr/backups` |
| `BACKUP_KEEP` | `30` | Daily snapshots to retain (older ones are purged) |
| `BACKUP_HOUR` | `3` | UTC hour to run the daily backup |
| `RESTORE_ON_START` | `false` | On boot, pull the latest snapshot **if the local DB is absent** |

Set at minimum: `BACKUP_RCLONE_REMOTE=nvbr-gdrive:nvbr/backups`.

### Step 5 — Start with the backup sidecar

```bash
cd deploy && docker compose --profile backup up -d --build
```

### Verify & operate

```bash
# Run a backup immediately (don't wait for the scheduled hour):
docker compose exec backup python -m app.backup
# Confirm a timestamped snapshot landed on Drive:
docker compose exec backup rclone lsf nvbr-gdrive:nvbr/backups
```

- **Backup:** `python -m app.backup` snapshots the DB + logs and pushes to
  `<BACKUP_RCLONE_REMOTE>/<YYYYMMDD-HHMMSS>/`; snapshots beyond `BACKUP_KEEP` are purged.
- **Daily sidecar:** `scripts/backup-loop.sh` sleeps until `BACKUP_HOUR:00 UTC`, then runs the
  backup; a failed run logs and continues (never crashes the loop).
- **Restore on startup:** with `RESTORE_ON_START=true`, a fresh VM (empty `nvbr_data` volume)
  pulls the latest snapshot before the models initialise. A failed restore logs and continues
  with a fresh DB — it never blocks startup. To force a restore, stop the app, clear the volume,
  set `RESTORE_ON_START=true`, and start again.

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
