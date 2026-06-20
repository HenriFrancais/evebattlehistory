"""FastAPI router for POST /api/fights/filter and POST /api/brs/filter."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.analytics.filters import FilterError, compile_br_filter, compile_fight_filter
from app.api.brs import _br_to_summary, compute_br_summary, enrich_summaries
from app.api.deps import SessionDep
from app.api.schemas import (
    BrFilterRequest,
    FightFilterRequest,
    FightSideOut,
    FightWithBrId,
    FilteredBrResponse,
)
from app.db.models import BrFight, Fight, FightSide

router = APIRouter()


@router.post("/api/fights/filter")
async def filter_fights(body: FightFilterRequest, session: SessionDep) -> list[FightWithBrId]:
    try:
        stmt = compile_fight_filter(body.tree)
    except FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.br_id is not None:
        # Scope to fights in this BR
        fight_id_subq = (
            select(BrFight.fight_id).where(BrFight.br_id == body.br_id).scalar_subquery()
        )
        stmt = stmt.where(Fight.fight_id.in_(fight_id_subq))

    result = await session.execute(stmt)
    fights = list(result.scalars())

    if not fights:
        return []

    fight_ids = [f.fight_id for f in fights]

    # Load sides
    side_result = await session.execute(
        select(FightSide).where(FightSide.fight_id.in_(fight_ids))
    )
    sides_by_fight: dict[int, list[FightSide]] = {}
    for side in side_result.scalars():
        sides_by_fight.setdefault(side.fight_id, []).append(side)

    # Load br_id for each fight
    brf_stmt = select(BrFight).where(BrFight.fight_id.in_(fight_ids))
    if body.br_id is not None:
        brf_stmt = brf_stmt.where(BrFight.br_id == body.br_id)
    brf_result = await session.execute(brf_stmt)
    br_id_by_fight: dict[int, str] = {}
    for brf in brf_result.scalars():
        br_id_by_fight[brf.fight_id] = brf.br_id

    out: list[FightWithBrId] = []
    for fight in fights:
        sides = [
            FightSideOut(
                side_idx=s.side_idx,
                side_kind=s.side_kind,
                pilot_count=s.pilot_count,
                isk_lost=s.isk_lost,
            )
            for s in sorted(sides_by_fight.get(fight.fight_id, []), key=lambda s: s.side_idx)
        ]
        out.append(FightWithBrId(
            fight_id=fight.fight_id,
            system_id=fight.system_id,
            started_at=fight.started_at,
            ended_at=fight.ended_at,
            isk_destroyed_total=fight.isk_destroyed_total,
            largest_side_pilots=fight.largest_side_pilots,
            capitals_involved=fight.capitals_involved,
            sides=sides,
            br_id=br_id_by_fight.get(fight.fight_id, ""),
        ))
    return out


@router.post("/api/brs/filter")
async def filter_brs(
    body: BrFilterRequest, session: SessionDep, request: Request
) -> FilteredBrResponse:
    try:
        stmt = compile_br_filter(body.tree)
    except FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await session.execute(stmt)
    brs = list(result.scalars())

    summary = compute_br_summary(brs)
    br_list = await enrich_summaries(session, request, [_br_to_summary(b) for b in brs])
    return FilteredBrResponse(summary=summary, brs=br_list)
