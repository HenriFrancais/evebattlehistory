"""Health endpoint for uptime probes (open, no auth)."""

from __future__ import annotations

import time

from fastapi import APIRouter

router = APIRouter()


class HealthState:
    roster_loaded: bool = False
    roster_version: int = 0
    roster_fetched_at: float = 0.0
    data_source: str = ""


HEALTH = HealthState()


@router.get("/healthz")
async def healthz() -> dict[str, object]:
    now = time.time()
    age = now - HEALTH.roster_fetched_at if HEALTH.roster_fetched_at else None
    return {
        "ok": True,
        "roster_loaded": HEALTH.roster_loaded,
        "roster_version": HEALTH.roster_version,
        "roster_age_s": age,
        "data_source": HEALTH.data_source,
    }
