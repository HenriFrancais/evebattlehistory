"""FastAPI router for per-character timeline endpoints (Task 3.1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import build_kill_events
from app.analytics.sides_config import load_overrides
from app.analytics.timeline import (
    character_timeline,
    character_timeline_events,
)
from app.api.access import acting_user, can_view_character
from app.api.deps import SessionDep
from app.api.schemas import (
    CharacterTimelineOut,
    KillEventOut,
    TimelineEventListOut,
    TimelineEventOut,
    TimelineFightInfo,
    TimelineSeriesOut,
)
from app.config import get_app_config
from app.db.models import BattleReport, BrFight

router = APIRouter()


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
    session: SessionDep,
    request: Request,
) -> CharacterTimelineOut:
    """Return the uPlot-aligned timeline for one character within a battle report.

    Returns empty series (not 404) when the character has no log buckets in the BR.
    404 if the battle report itself is unknown.
    403 if the acting user may not view this character's data.
    """
    user = await acting_user(request)
    if not await can_view_character(user, character_id):
        raise HTTPException(status_code=403, detail="Access denied: not your character")
    await _require_br(br_id, session)
    tl = await character_timeline(session, br_id, character_id)

    # Same BR-wide kill marks as the fleet view (overlay, not character-scoped).
    cfg = get_app_config()
    overrides = await load_overrides(session, br_id)
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    kills = await build_kill_events(
        session, fight_ids, set(cfg.our_alliance_ids), set(cfg.our_corp_ids), overrides
    )

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
        kills=[
            KillEventOut(
                ts=k.ts,
                killmail_id=k.killmail_id,
                victim_character_id=k.victim_character_id,
                victim_character_name=k.victim_character_name,
                victim_ship_name=k.victim_ship_name,
                victim_ship_type_id=k.victim_ship_type_id,
                side_kind=k.side_kind,
                isk=k.isk,
            )
            for k in kills
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
    session: SessionDep,
    request: Request,
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
    403 if the acting user may not view this character's data.
    """
    user = await acting_user(request)
    if not await can_view_character(user, character_id):
        raise HTTPException(status_code=403, detail="Access denied: not your character")
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
