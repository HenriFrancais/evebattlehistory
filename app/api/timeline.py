"""FastAPI router for per-character timeline endpoints (Task 3.1)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.timeline import (
    character_timeline,
    character_timeline_events,
)
from app.api.schemas import (
    CharacterTimelineOut,
    TimelineEventListOut,
    TimelineEventOut,
    TimelineFightInfo,
    TimelineSeriesOut,
)
from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport

router = APIRouter()


# No shared session dependency exists in this project: brs.py and logs.py each
# define their own inline session creation via get_sessionmaker().  This local
# _get_session follows the same pattern for consistency.
async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    settings = get_settings()
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        yield session


_Session = Annotated[AsyncSession, Depends(_get_session)]


async def _require_br(br_id: str, session: AsyncSession) -> None:
    """Raise 404 if the BR does not exist."""
    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")


@router.get("/api/brs/{br_id}/characters/{character_id}/timeline")
async def get_character_timeline(
    br_id: str,
    character_id: int,
    session: _Session,
) -> CharacterTimelineOut:
    """Return the uPlot-aligned timeline for one character within a battle report.

    Returns empty series (not 404) when the character has no log buckets in the BR.
    404 if the battle report itself is unknown.
    """
    await _require_br(br_id, session)
    tl = await character_timeline(session, br_id, character_id)

    return CharacterTimelineOut(
        x=tl.x,
        series=[
            TimelineSeriesOut(
                key=s.key,
                effect_type=s.effect_type,
                direction=s.direction,
                values=s.values,
                event_count=s.event_count,
            )
            for s in tl.series
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
        t_start=tl.t_start,
        t_end=tl.t_end,
    )


@router.get("/api/brs/{br_id}/characters/{character_id}/events")
async def get_character_events(
    br_id: str,
    character_id: int,
    session: _Session,
    t_from: int = Query(..., alias="from"),
    t_to: int = Query(..., alias="to"),
    effect_type: str | None = Query(default=None),
    direction: str | None = Query(default=None),
) -> TimelineEventListOut:
    """Return raw log events for one character in a time slice.

    Query params:
    - ``from`` (epoch seconds, required)
    - ``to`` (epoch seconds, required; must be >= from)
    - ``effect_type`` (optional filter)
    - ``direction`` (optional filter)

    Results are ordered by ts ascending and capped at 1000 rows.
    When capped, ``truncated=true`` is set on the response.
    404 if the battle report is unknown; 400 if from > to.
    """
    if t_from > t_to:
        raise HTTPException(status_code=400, detail="'from' must be <= 'to'")

    await _require_br(br_id, session)

    result = await character_timeline_events(
        session,
        br_id,
        character_id,
        t_from=t_from,
        t_to=t_to,
        effect_type=effect_type,
        direction=direction,
    )

    return TimelineEventListOut(
        events=[
            TimelineEventOut(
                ts=e.ts,
                direction=e.direction,
                effect_type=e.effect_type,
                amount=e.amount,
                quality=e.quality,
                other_name=e.other_name,
                other_ship_name=e.other_ship_name,
                module_name=e.module_name,
            )
            for e in result.events
        ],
        truncated=result.truncated,
    )
