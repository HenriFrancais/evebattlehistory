"""Health endpoint for uptime probes (open, no auth)."""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


class HealthState:
    roster_loaded: bool = False
    roster_version: int = 0
    roster_fetched_at: float = 0.0
    data_source: str = ""
    #: Ship/entity types loaded from the SDE; 0 means log-name parsing is degraded.
    sde_types: int = 0


HEALTH = HealthState()

_schema_version = 0


async def _db_ok() -> bool:
    """True iff the database answers a trivial query (also caches schema version)."""
    global _schema_version
    from app.config import get_settings
    from app.db.engine import get_engine

    try:
        async with get_engine(get_settings()).connect() as conn:
            row = (await conn.exec_driver_sql("PRAGMA user_version")).fetchone()
            _schema_version = int(row[0]) if row else 0
        return True
    except Exception:
        return False


@router.get("/healthz")
async def healthz() -> JSONResponse:
    now = time.time()
    age = now - HEALTH.roster_fetched_at if HEALTH.roster_fetched_at else None
    db = await _db_ok()
    body = {
        "ok": db,
        "db": db,
        "schema_version": _schema_version,
        "sde_types": HEALTH.sde_types,
        "roster_loaded": HEALTH.roster_loaded,
        "roster_version": HEALTH.roster_version,
        "roster_age_s": age,
        "data_source": HEALTH.data_source,
    }
    # 503 makes the container healthcheck (and deploy.sh's wait) fail when the
    # database is unreachable, instead of reporting a dead app as healthy.
    return JSONResponse(body, status_code=200 if db else 503)
