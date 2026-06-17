"""FastAPI router for Battle Report CRUD and status endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import can_create_br, current_user
from app.api.schemas import (
    BrCreate,
    BrCreated,
    BrDetail,
    BrListResponse,
    BrListSummary,
    BrStatus,
    BrSummary,
    FightOut,
    FightSideOut,
)
from app.config import get_settings
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport, BrFight, Fight, FightSide
from app.ingest.jobs import schedule_ingest
from app.logs.coverage import _coverage_to_dict, br_coverage, my_coverage
from app.observability.logging import log

SUPPORTED_HOSTS = {"zkillboard.com", "br.evetools.org"}

router = APIRouter()


async def _get_session() -> AsyncGenerator[AsyncSession, None]:
    settings = get_settings()
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        yield session


_Session = Annotated[AsyncSession, Depends(_get_session)]


@router.post("/api/brs", status_code=202)
async def create_br(
    body: BrCreate,
    request: Request,
    session: _Session,
) -> BrCreated:
    """Submit a new battle report URL for ingestion."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    host = urlparse(body.url).netloc
    bare_host = host.removeprefix("www.")
    if bare_host not in SUPPORTED_HOSTS:
        raise HTTPException(status_code=400, detail=f"Unsupported URL host: {host}")

    settings = get_settings()
    br_id = str(uuid.uuid4())
    char_id_str = user.main_character_id
    char_id = int(char_id_str) if char_id_str and char_id_str.isdigit() else None

    br = BattleReport(
        br_id=br_id,
        source="",
        source_url=body.url,
        source_ref="",
        title=body.title,
        created_by_user=user.user_name,
        created_by_char_id=char_id,
        status="pending",
        progress_pct=0,
        created_at=dt.datetime.now(dt.UTC),
    )
    session.add(br)
    await session.commit()

    log.info("brs.created", br_id=br_id, url=body.url, user=user.user_name)
    schedule_ingest(settings, br_id)

    return BrCreated(br_id=br_id, status="pending")


@router.get("/api/brs")
async def list_brs(
    session: _Session,
) -> BrListResponse:
    """Return a list of all battle reports with aggregate summary."""
    result = await session.execute(
        select(BattleReport).order_by(
            BattleReport.battle_at.desc().nulls_last(),
            BattleReport.created_at.desc(),
        )
    )
    brs = list(result.scalars())

    wins = sum(1 for b in brs if b.result == "win")
    ties = sum(1 for b in brs if b.result == "tie")
    losses = sum(1 for b in brs if b.result == "loss")
    decided = sum(1 for b in brs if b.result is not None)
    win_rate = wins / decided if decided > 0 else 0.0

    total_isk_destroyed = sum(b.our_isk_destroyed for b in brs)
    total_isk_lost = sum(b.our_isk_lost for b in brs)

    summary = BrListSummary(
        total=len(brs),
        wins=wins,
        ties=ties,
        losses=losses,
        win_rate=win_rate,
        total_isk_destroyed=total_isk_destroyed,
        total_isk_lost=total_isk_lost,
    )

    br_list = [_br_to_summary(b) for b in brs]
    return BrListResponse(summary=summary, brs=br_list)


@router.get("/api/brs/{br_id}")
async def get_br(
    br_id: str,
    session: _Session,
) -> BrDetail:
    """Return full detail for one battle report, including fights."""
    result = await session.execute(
        select(BattleReport).where(BattleReport.br_id == br_id)
    )
    br = result.scalar_one_or_none()
    if br is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    fights = await _load_fights(session, br_id)

    return BrDetail(
        **_br_to_summary(br).model_dump(),
        fights=fights,
    )


@router.get("/api/brs/{br_id}/status")
async def get_br_status(
    br_id: str,
    session: _Session,
) -> BrStatus:
    """Return the current ingest status for a battle report."""
    result = await session.execute(
        select(BattleReport).where(BattleReport.br_id == br_id)
    )
    br = result.scalar_one_or_none()
    if br is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    return BrStatus(
        br_id=br.br_id,
        status=br.status,
        progress_pct=br.progress_pct,
        error_text=br.error_text,
    )


@router.get("/api/brs/{br_id}/fights")
async def get_br_fights(
    br_id: str,
    session: _Session,
) -> list[FightOut]:
    """Return the fights for a battle report."""
    result = await session.execute(
        select(BattleReport.br_id).where(BattleReport.br_id == br_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    return await _load_fights(session, br_id)


@router.get("/api/brs/{br_id}/coverage")
async def get_br_coverage(
    br_id: str,
    request: Request,
    session: _Session,
) -> list[dict]:  # type: ignore[type-arg]
    """Return per-user/character log coverage matrix for a battle report.

    404 if the BR doesn't exist.  Requires authentication (all members may read).
    """
    current_user(request)  # auth check
    settings = get_settings()

    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    coverage = await br_coverage(session, settings, br_id)
    return [_coverage_to_dict(uc) for uc in coverage]


@router.get("/api/brs/{br_id}/my-coverage")
async def get_my_br_coverage(
    br_id: str,
    request: Request,
    session: _Session,
) -> dict:  # type: ignore[type-arg]
    """Return the current user's log coverage for a battle report.

    404 if the BR doesn't exist or the user has no participating characters.
    """
    user = current_user(request)
    settings = get_settings()

    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    cov = await my_coverage(session, settings, br_id, user.user_name)
    if cov is None:
        raise HTTPException(
            status_code=404, detail="No participating characters for this user in this BR"
        )
    return _coverage_to_dict(cov)


def _br_to_summary(br: BattleReport) -> BrSummary:
    return BrSummary(
        br_id=br.br_id,
        title=br.title,
        source=br.source,
        source_url=br.source_url,
        status=br.status,
        progress_pct=br.progress_pct,
        result=br.result,
        isk_efficiency=br.isk_efficiency,
        our_isk_destroyed=br.our_isk_destroyed,
        our_isk_lost=br.our_isk_lost,
        fight_count=br.fight_count,
        battle_at=br.battle_at,
        created_at=br.created_at,
    )


async def _load_fights(session: AsyncSession, br_id: str) -> list[FightOut]:
    """Load Fight + FightSide rows for the given BR, ordered by seq."""
    bf_result = await session.execute(
        select(BrFight).where(BrFight.br_id == br_id).order_by(BrFight.seq)
    )
    br_fights = list(bf_result.scalars())
    if not br_fights:
        return []

    fight_ids = [bf.fight_id for bf in br_fights]

    fight_result = await session.execute(
        select(Fight).where(Fight.fight_id.in_(fight_ids))
    )
    fights_by_id: dict[int, Fight] = {f.fight_id: f for f in fight_result.scalars()}

    side_result = await session.execute(
        select(FightSide).where(FightSide.fight_id.in_(fight_ids))
    )
    sides_by_fight: dict[int, list[FightSide]] = {}
    for side in side_result.scalars():
        sides_by_fight.setdefault(side.fight_id, []).append(side)

    out: list[FightOut] = []
    for bf in br_fights:
        fight = fights_by_id.get(bf.fight_id)
        if fight is None:
            continue
        sides = [
            FightSideOut(
                side_idx=s.side_idx,
                side_kind=s.side_kind,
                pilot_count=s.pilot_count,
                isk_lost=s.isk_lost,
            )
            for s in sorted(sides_by_fight.get(fight.fight_id, []), key=lambda s: s.side_idx)
        ]
        out.append(FightOut(
            fight_id=fight.fight_id,
            system_id=fight.system_id,
            started_at=fight.started_at,
            ended_at=fight.ended_at,
            isk_destroyed_total=fight.isk_destroyed_total,
            largest_side_pilots=fight.largest_side_pilots,
            sides=sides,
        ))

    return out
