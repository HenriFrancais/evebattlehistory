"""FastAPI router for the fleet-level timeline endpoint (E3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import fleet_timeline
from app.api.deps import SessionDep
from app.api.schemas import (
    FleetSeriesOut,
    FleetTimelineOut,
    KillEventOut,
    TimelineFightInfo,
)
from app.db.models import BattleReport

router = APIRouter()


async def _require_br(br_id: str, session: AsyncSession) -> None:
    """Raise 404 if the BR does not exist."""
    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")


@router.get("/api/brs/{br_id}/fleet-timeline")
async def get_fleet_timeline(br_id: str, session: SessionDep) -> FleetTimelineOut:
    """Return the aggregated fleet timeline for *br_id*.

    No per-character access gate — visible to all authenticated users.
    """
    await _require_br(br_id, session)
    tl = await fleet_timeline(session, br_id)

    return FleetTimelineOut(
        x=tl.x,
        series=[FleetSeriesOut(key=s.key, values=s.values) for s in tl.series],
        kills=[
            KillEventOut(
                ts=k.ts,
                killmail_id=k.killmail_id,
                victim_character_id=k.victim_character_id,
                victim_ship_name=k.victim_ship_name,
                side_kind=k.side_kind,
                isk=k.isk,
            )
            for k in tl.kills
        ],
        fights=[
            TimelineFightInfo(
                fight_id=f.fight_id,
                seq=f.seq,
                started_at=f.started_at,
                ended_at=f.ended_at,
                system_id=f.system_id,
            )
            for f in tl.fights
        ],
        bucket_seconds=tl.bucket_seconds,
        t_start=tl.t_start,
        t_end=tl.t_end,
    )
