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

# Run unprivileged. Give the user ownership of the data mountpoint so it can
# write the SQLite DB and logs as non-root.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var \
    && chown -R appuser /app/var

EXPOSE 8000
USER appuser

# Bind to 127.0.0.1 only — Caddy on the VM terminates TLS and reverse-proxies here.
# Override with the `command:` key in docker-compose.yml if needed.
CMD ["sh", "-c", \
     "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 127.0.0.1:8000"]
