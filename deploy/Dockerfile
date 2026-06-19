# Multistage build: 1) Vite frontend, 2) Python runtime with rclone.
#
# Build args:
#   VITE_URL_PREFIX  — e.g. "/br". Must match Settings.url_prefix at runtime.

ARG VITE_URL_PREFIX=/br

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

# Install uv then use it to install the project (pip-style, not uv.lock sync,
# so the image doesn't depend on uv.lock being present at build time).
RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
RUN uv pip install --system --no-cache .

# Copy the built SPA from stage 1.
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# Process the CCP SDE once per build, cached by build number across builds.
# The BuildKit cache mount makes the ~80MB download + processing free on rebuilds
# whose CCP build hasn't advanced. We bake the processed artifact into a path
# OUTSIDE /app/var, because at runtime the `nvbr_data` volume mounts over
# /app/var and would shadow anything baked there. The CMD copies the baked files
# into the runtime SDE_DIR (./var/sde → /app/var/sde) after the volume is mounted.
RUN --mount=type=cache,target=/app/var/sde \
    python -m app.sde.refresh \
    && mkdir -p /app/sde-baked \
    && cp /app/var/sde/inventory_types.jsonl /app/var/sde/manifest.json /app/sde-baked/ 2>/dev/null || true

# Run unprivileged. Give the user ownership of the data mountpoint so it can
# write the SQLite DB and logs as non-root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser /app/var /app/sde-baked

EXPOSE 8000
USER appuser

# Bind to 127.0.0.1 only — Caddy on the VM terminates TLS and reverse-proxies here.
# Override with the `command:` key in docker-compose.yml if needed.
# Seed the runtime SDE_DIR (./var/sde under the data volume) from the baked
# artifact on first boot, then start the server. The startup loader reads $SDE_DIR
# and loads the artifact into the DB if the build advanced.
CMD ["sh", "-c", \
     "mkdir -p /app/var/sde \
      && cp -n /app/sde-baked/inventory_types.jsonl /app/sde-baked/manifest.json /app/var/sde/ 2>/dev/null || true; \
      exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 127.0.0.1:8000"]
