"""FastAPI router for the fleet-level timeline endpoint (E3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.composition import fleet_composition
from app.analytics.fleet import fleet_contributions, fleet_timeline
from app.analytics.sides_config import load_overrides
from app.api.access import acting_user
from app.api.auth import can_create_br
from app.api.deps import SessionDep
from app.config import get_app_config, get_settings
from app.api.schemas import (
    CompositionOut,
    CompositionPilotOut,
    CompositionShipOut,
    CompositionSideOut,
    ContributionOut,
    ContributionsOut,
    FleetSeriesOut,
    FleetTimelineOut,
    KillEventOut,
    TimelineFightInfo,
)
from app.db.models import BUCKET_SECONDS, BattleReport
from app.roster.snapshot import get_roster_store

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
    cfg = get_app_config()
    overrides = await load_overrides(session, br_id)
    tl = await fleet_timeline(
        session, br_id, cfg.our_alliance_ids, cfg.our_corp_ids, overrides
    )

    return FleetTimelineOut(
        x=tl.x,
        series=[
            FleetSeriesOut(
                key=s.key,
                effect_type=s.effect_type,
                direction=s.direction,
                metric=s.metric,
                values=s.values,
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


@router.get("/api/brs/{br_id}/contributions")
async def get_contributions(br_id: str, session: SessionDep, at: int) -> ContributionsOut:
    """All source→target activity at a single time bucket, grouped by type.

    `at` = epoch seconds (a bucket timestamp).
    """
    await _require_br(br_id, session)
    contribs = await fleet_contributions(session, br_id, at, get_settings())
    return ContributionsOut(
        at=at,
        bucket_seconds=BUCKET_SECONDS,
        rows=[
            ContributionOut(
                source_character_id=c.source_character_id,
                source_name=c.source_name,
                target_name=c.target_name,
                effect_type=c.effect_type,
                direction=c.direction,
                group=c.group,
                value=c.value,
                module_name=c.module_name,
                icon_type_id=c.icon_type_id,
                weapon_category=c.weapon_category,
            )
            for c in contribs
        ],
    )


@router.get("/api/brs/{br_id}/composition")
async def get_composition(
    br_id: str, request: Request, session: SessionDep
) -> CompositionOut:
    """Per-side fleet composition. Elevated callers (FC/HC) also get char→user."""
    await _require_br(br_id, session)
    cfg = get_app_config()
    settings = get_settings()
    acting = await acting_user(request, settings)
    char_to_user: dict[int, str] | None = None
    by_user_available = False
    if can_create_br(acting):
        try:
            roster = await get_roster_store(settings).get()
            char_to_user = dict(roster.char_to_user)
            by_user_available = True
        except Exception:  # roster unavailable → no user grouping
            char_to_user = None
            by_user_available = False
    overrides = await load_overrides(session, br_id)
    result = await fleet_composition(
        session, br_id,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
        overrides=overrides, settings=settings, char_to_user=char_to_user,
    )
    return CompositionOut(
        by_user_available=by_user_available,
        sides=[
            CompositionSideOut(
                side_kind=s.side_kind,
                pilot_count=s.pilot_count,
                ships=[CompositionShipOut(ship_type_id=sh.ship_type_id,
                                          ship_name=sh.ship_name, count=sh.count)
                       for sh in s.ships],
                pilots=[CompositionPilotOut(character_id=p.character_id,
                                            character_name=p.character_name,
                                            ship_type_id=p.ship_type_id, ship_name=p.ship_name,
                                            lost=p.lost, user_name=p.user_name) for p in s.pilots],
            )
            for s in result.sides
        ],
    )
