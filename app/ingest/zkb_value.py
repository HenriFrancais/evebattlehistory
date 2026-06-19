"""Backfill Killmail.total_value from zKillboard's per-killmail endpoint.

zKill persists the ISK value it calculates at time of destruction. The /related/
resolver captures it when present; this module fills any killmail still missing a
value via GET /api/killID/{id}/, politely (bounded concurrency).
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import BrKillmail, Killmail
from app.observability.logging import log

ZKB_API = "https://zkillboard.com/api"
_MAX_CONCURRENCY = 4


async def _fetch_value(client: httpx.AsyncClient, km_id: int, km_hash: str | None) -> float | None:
    """Return zkb.totalValue for one killmail, or None on any failure."""
    try:
        resp = await client.get(f"{ZKB_API}/killID/{km_id}/")
        if resp.status_code != 200:
            return None
        data = resp.json()
        # /killID/ returns a list with one package: [{"killmail_id":..,"zkb":{"totalValue":..}}]
        pkg = data[0] if isinstance(data, list) and data else data
        zkb = pkg.get("zkb") if isinstance(pkg, dict) else None
        tv = zkb.get("totalValue") if isinstance(zkb, dict) else None
        return float(tv) if isinstance(tv, (int, float)) else None
    except Exception as exc:  # network / shape / json
        log.warning("zkb.value_fetch_failed", km_id=km_id, error=str(exc))
        return None


async def backfill_killmail_values(
    session: AsyncSession, br_id: str, settings: Settings
) -> int:
    """Fill null Killmail.total_value for the BR's killmails from zKill. Returns count updated."""
    if settings.data_source == "demo":
        return 0
    rows = (
        await session.execute(
            select(Killmail.killmail_id, Killmail.hash)
            .join(BrKillmail, BrKillmail.killmail_id == Killmail.killmail_id)
            .where(BrKillmail.br_id == br_id, Killmail.total_value.is_(None))
        )
    ).all()
    if not rows:
        return 0

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    updated = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "nv-br", "Accept-Encoding": "gzip"}, timeout=30.0
    ) as client:
        async def _one(km_id: int, km_hash: str | None) -> None:
            nonlocal updated
            async with sem:
                value = await _fetch_value(client, km_id, km_hash)
            if value is not None:
                await session.execute(
                    update(Killmail).where(Killmail.killmail_id == km_id).values(total_value=value)
                )
                updated += 1

        await asyncio.gather(*[_one(int(kid), kh) for kid, kh in rows])
    return updated
