# Multistage build: 1) Vite frontend, 2) Python runtime with rclone.
#
# Build args:
#   VITE_URL_PREFIX  — e.g. "/fc/br". Must match Settings.url_prefix at runtime.

ARG VITE_URL_PREFIX=/fc/br

# ── Stage 1: Build the Vite SPA ─────────────────────────────────────────────
FROM node:22-slim AS frontend-build

ARG VITE_URL_PREFIX
ENV VITE_URL_PREFIX=$VITE_URL_PREFIX

WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# rclone + ca-certificates: needed by the backup sidecar compose profile.
# bash + GNU date (for backup-loop.sh) ship with debian-slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv, then the EXACT dependency versions pinned in uv.lock (so an image
# built next month runs the same library versions that were tested), then the
# project itself without re-resolving anything.
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN uv pip install --system --no-cache --no-deps .

# App/fleet config (who may create BRs, the "us" entity set). Committed safe
# defaults; edit config.toml and rebuild to change them.
COPY config.toml ./

# Runtime data assets read from disk at /app/data (e.g. capital_ships.json,
# loaded by app/fights/capitals.py). Not part of the installed wheel, so copy
# explicitly. Placed after the install so editing data doesn't bust that layer.
COPY data ./data

# Copy the built SPA from stage 1.
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# Process the CCP SDE once per build, cached by build number across builds.
# The BuildKit cache mount makes the ~80MB download + processing free on rebuilds
# whose CCP build hasn't advanced. We bake the processed artifact into a path
# OUTSIDE /app/var, because at runtime the `nvbr_data` volume mounts over
# /app/var and would shadow anything baked there. The CMD copies the baked files
# into the runtime SDE_DIR (./var/sde → /app/var/sde) after the volume is mounted.
#
# The SDE is the ship-name dictionary the log parser needs; an image without it
# ingests logs with degraded pilot/ship names. So a failed download FAILS THE
# BUILD. To ship an urgent fix while CCP's download is down, build with
# `--build-arg SDE_REQUIRED=0` (the volume's existing SDE keeps being used).
ARG SDE_REQUIRED=1
RUN --mount=type=cache,target=/app/var/sde \
    mkdir -p /app/sde-baked; \
    if python -m app.sde.refresh \
        && [ -s /app/var/sde/inventory_types.jsonl ] && [ -s /app/var/sde/manifest.json ]; then \
      cp /app/var/sde/inventory_types.jsonl /app/var/sde/manifest.json /app/sde-baked/; \
    elif [ "$SDE_REQUIRED" = "0" ]; then \
      echo "WARNING: SDE not baked into this image (SDE_REQUIRED=0)"; \
    else \
      echo "ERROR: could not fetch/process the CCP SDE; rebuild, or pass --build-arg SDE_REQUIRED=0" >&2; \
      exit 1; \
    fi

# Run unprivileged. Give the user ownership of the data mountpoint so it can
# write the SQLite DB and logs as non-root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser /app/var /app/sde-baked

EXPOSE 8000
USER appuser

# Bind 0.0.0.0:8000 INSIDE the container (the container network namespace is
# isolated). Public exposure is restricted by docker-compose publishing the port
# to the host's loopback only (127.0.0.1:${APP_PORT}:8000), so host-side Caddy can
# reach it while the outside world cannot. Binding 127.0.0.1 here would make the
# published port unreachable from the host.
# ONE worker, deliberately. The app is async (one process handles concurrent
# requests) and SQLite has a single writer; the ingest lock, the pending-ingest
# sweep, restore-on-start and the off-BR cache all live in process memory. With
# more than one worker every one of those runs/diverges per process (concurrent
# ingests of the same BR, "database is locked", stale caches). Do not raise -w
# without first moving that coordination into the database.
# --timeout 120: the worker heartbeat rides on the event loop, so with a single
# worker a request that blocks the loop past gunicorn's default 30 s would get the
# whole app killed and restarted mid-ingest.
# Copy the baked SDE artifact into the runtime SDE_DIR (./var/sde under the data
# volume) on EVERY boot — not only the first — so a rebuilt image with a newer CCP
# build replaces the volume's copy; then start the server. The startup loader reads $SDE_DIR
# and loads the artifact into the DB if the build advanced.
CMD ["sh", "-c", \
     "mkdir -p /app/var/sde; \
      if [ -s /app/sde-baked/manifest.json ]; then \
        cp /app/sde-baked/inventory_types.jsonl /app/sde-baked/manifest.json /app/var/sde/; \
      fi; \
      exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 1 --timeout 120 --graceful-timeout 30 -b 0.0.0.0:8000"]
