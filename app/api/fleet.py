"""FastAPI router for the fleet-level timeline endpoint (E3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.composition import fleet_composition
from app.analytics.fleet import fleet_snapshot, fleet_timeline
from app.analytics.sides_config import load_overrides
from app.api.access import acting_user, can_view_character
from app.api.auth import can_create_br
from app.api.deps import SessionDep
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
    LeaderEntryOut,
    LeadersOut,
    TimelineFightInfo,
    WeaponEffectOut,
)
from app.config import get_app_config, get_settings
from app.db.models import BattleReport
from app.observability.logging import log
from app.roster.snapshot import get_roster_store

router = APIRouter()


def _leader_out(e: object) -> LeaderEntryOut | None:
    from app.analytics.fleet import LeaderEntry

    if e is None:
        return None
    if not isinstance(e, LeaderEntry):
        raise TypeError(f"Expected LeaderEntry, got {type(e)!r}")
    return LeaderEntryOut(
        name=e.name, ship=e.ship, amount=e.amount, ship_type_id=e.ship_type_id
    )


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
        leaders=[
            LeadersOut(
                top_friendly_dmg_taken=_leader_out(ld.top_friendly_dmg_taken),
                top_hostile_dmg_taken=_leader_out(ld.top_hostile_dmg_taken),
                top_friendly_rep_recv=_leader_out(ld.top_friendly_rep_recv),
            )
            for ld in tl.leaders
        ],
    )


@router.get("/api/brs/{br_id}/snapshot")
async def get_snapshot(
    br_id: str, session: SessionDep, from_ts: int, to_ts: int
) -> ContributionsOut:
    """All source→target activity in the half-open window [from_ts, to_ts)."""
    await _require_br(br_id, session)
    contribs = await fleet_snapshot(session, br_id, from_ts, to_ts, get_settings())
    return ContributionsOut(
        from_ts=from_ts,
        to_ts=to_ts,
        rows=[
            ContributionOut(
                source_character_id=c.source_character_id,
                source_name=c.source_name,
                target_name=c.target_name,
                target_ship=c.target_ship,
                effect_type=c.effect_type,
                direction=c.direction,
                group=c.group,
                value=c.value,
                module_name=c.module_name,
                icon_type_id=c.icon_type_id,
                weapon_category=c.weapon_category,
                quality=c.quality,
            )
            for c in contribs
        ],
    )


@router.get("/api/brs/{br_id}/characters/{character_id}/snapshot")
async def get_character_snapshot(
    br_id: str,
    character_id: int,
    request: Request,
    session: SessionDep,
    from_ts: int,
    to_ts: int,
) -> ContributionsOut:
    """One character's source↔target activity in [from_ts, to_ts).

    Access-gated like the per-character timeline: elevated users (FC/HC) may view
    anyone; others only their own characters.
    """
    await _require_br(br_id, session)
    settings = get_settings()
    acting = await acting_user(request, settings)
    if not await can_view_character(acting, character_id, settings):
        raise HTTPException(status_code=403, detail="Not permitted to view this character")
    contribs = await fleet_snapshot(
        session, br_id, from_ts, to_ts, settings, character_id=character_id
    )
    return ContributionsOut(
        from_ts=from_ts,
        to_ts=to_ts,
        rows=[
            ContributionOut(
                source_character_id=c.source_character_id,
                source_name=c.source_name,
                target_name=c.target_name,
                target_ship=c.target_ship,
                effect_type=c.effect_type,
                direction=c.direction,
                group=c.group,
                value=c.value,
                module_name=c.module_name,
                icon_type_id=c.icon_type_id,
                weapon_category=c.weapon_category,
                quality=c.quality,
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
        except Exception as exc:  # roster unavailable → no user grouping (fail closed)
            log.warning("composition.roster_unavailable", br_id=br_id, error=str(exc))
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
                                            lost=p.lost, reship=p.reship,
                                            killmail_id=p.killmail_id,
                                            user_name=p.user_name,
                                            weapons=[WeaponEffectOut(type_id=w.type_id,
                                                                     name=w.name,
                                                                     role=w.role)
                                                     for w in p.weapons]) for p in s.pilots],
            )
            for s in result.sides
        ],
    )
