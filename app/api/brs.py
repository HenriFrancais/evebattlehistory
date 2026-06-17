"""FastAPI router for Battle Report CRUD and status endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import can_create_br, current_user
from app.api.deps import SessionDep
from app.api.schemas import (
    BrCreate,
    BrCreated,
    BrDetail,
    BrListResponse,
    BrListSummary,
    BrPatch,
    BrSourceIn,
    BrSourceOut,
    BrStatus,
    BrSummary,
    FightOut,
    FightSideOut,
)
from app.config import get_settings
from app.db.models import BattleReport, BrFight, BrSource, Fight, FightSide
from app.fights.participants import ParticipantInfo, br_participants
from app.ingest.jobs import schedule_ingest
from app.logs.coverage import _coverage_to_dict, br_coverage, my_coverage
from app.observability.logging import log

SUPPORTED_HOSTS = {"zkillboard.com", "br.evetools.org"}

router = APIRouter()


def _validate_sources(sources: list[BrSourceIn]) -> None:
    """Validate source entries; raises HTTPException 400 on invalid input.

    For link sources: require a url.  Host validation is intentionally relaxed
    here — unsupported hosts will error-isolate at resolve time (per-source).
    For window sources: require system_id, window_start < window_end.
    """
    for src in sources:
        if src.kind == "link":
            if not src.url:
                raise HTTPException(status_code=400, detail="link source requires url")
        elif src.kind == "window":
            if src.system_id is None:
                raise HTTPException(status_code=400, detail="window source requires system_id")
            if src.window_start is None or src.window_end is None:
                raise HTTPException(
                    status_code=400,
                    detail="window source requires window_start and window_end",
                )
            if src.window_start >= src.window_end:
                raise HTTPException(
                    status_code=400,
                    detail="window_start must be before window_end",
                )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source kind: {src.kind!r}")


def _add_br_sources(
    session: AsyncSession,
    br_id: str,
    sources: list[BrSourceIn],
) -> list[BrSource]:
    """Create BrSource rows (not yet committed)."""
    rows: list[BrSource] = []
    now = dt.datetime.now(dt.UTC)
    for src in sources:
        row = BrSource(
            br_id=br_id,
            kind=src.kind,
            url=src.url if src.kind == "link" else None,
            system_id=src.system_id if src.kind == "window" else None,
            window_start=src.window_start if src.kind == "window" else None,
            window_end=src.window_end if src.kind == "window" else None,
            label=src.label,
            status="pending",
            km_count=0,
            created_at=now,
        )
        session.add(row)
        rows.append(row)
    return rows


@router.post("/api/brs", status_code=202)
async def create_br(
    body: BrCreate,
    request: Request,
    session: SessionDep,
) -> BrCreated:
    """Submit a new battle report for ingestion.

    Accepts either:
    - Back-compat: {url, title?} → creates one link BrSource
    - Multi-source: {sources:[...], title?} → creates one BrSource per entry
    """
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Normalise to a list of BrSourceIn
    if body.sources is not None:
        sources = body.sources
        _validate_sources(sources)
    elif body.url is not None:
        # Back-compat single-URL path: still validate the host eagerly
        host = urlparse(body.url).netloc.removeprefix("www.")
        if host not in SUPPORTED_HOSTS:
            raise HTTPException(status_code=400, detail=f"Unsupported URL host: {host}")
        sources = [BrSourceIn(kind="link", url=body.url)]
    else:
        raise HTTPException(status_code=400, detail="Provide either 'url' or 'sources'")

    settings = get_settings()
    br_id = str(uuid.uuid4())
    char_id_str = user.main_character_id
    char_id = int(char_id_str) if char_id_str and char_id_str.isdigit() else None

    # Derive source_url for back-compat from the first link source
    primary_url = next(
        (s.url for s in sources if s.kind == "link" and s.url),
        f"multi-source:{br_id}",
    )

    br = BattleReport(
        br_id=br_id,
        source="",
        source_url=primary_url,
        source_ref="",
        title=body.title,
        created_by_user=user.user_name,
        created_by_char_id=char_id,
        status="pending",
        progress_pct=0,
        created_at=dt.datetime.now(dt.UTC),
    )
    session.add(br)
    _add_br_sources(session, br_id, sources)
    await session.commit()

    log.info("brs.created", br_id=br_id, source_count=len(sources), user=user.user_name)
    schedule_ingest(settings, br_id)

    return BrCreated(br_id=br_id, status="pending")


# ---------------------------------------------------------------------------
# E4a: PATCH title, GET/POST/DELETE sources, POST refresh
# ---------------------------------------------------------------------------


@router.patch("/api/brs/{br_id}", status_code=200)
async def patch_br(
    br_id: str,
    body: BrPatch,
    request: Request,
    session: SessionDep,
) -> BrSummary:
    """Update a BR's title. Gated: only users who can_create_br."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
    br = result.scalar_one_or_none()
    if br is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    br.title = body.title
    await session.commit()
    return _br_to_summary(br)


@router.get("/api/brs/{br_id}/sources")
async def get_br_sources(
    br_id: str,
    session: SessionDep,
) -> list[BrSourceOut]:
    """Return all sources for a BR."""
    # Verify BR exists
    br_check = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if br_check is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    rows = list(
        (
            await session.execute(select(BrSource).where(BrSource.br_id == br_id))
        ).scalars()
    )
    return [_source_to_out(s) for s in rows]


@router.post("/api/brs/{br_id}/sources", status_code=202)
async def add_br_source(
    br_id: str,
    body: BrSourceIn,
    request: Request,
    session: SessionDep,
) -> BrCreated:
    """Add a source to an existing BR and trigger a refresh ingest. Gated."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    br_check = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if br_check is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    _validate_sources([body])
    _add_br_sources(session, br_id, [body])
    await session.commit()

    settings = get_settings()
    schedule_ingest(settings, br_id)

    return BrCreated(br_id=br_id, status="pending")


@router.delete("/api/brs/{br_id}/sources/{source_id}", status_code=204)
async def delete_br_source(
    br_id: str,
    source_id: int,
    request: Request,
    session: SessionDep,
) -> None:
    """Remove a source from a BR and trigger a refresh ingest. Gated."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    br_check = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if br_check is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    src = (
        await session.execute(
            select(BrSource).where(
                BrSource.source_id == source_id, BrSource.br_id == br_id
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")

    await session.delete(src)
    await session.commit()

    settings = get_settings()
    schedule_ingest(settings, br_id)


@router.post("/api/brs/{br_id}/refresh", status_code=202)
async def refresh_br(
    br_id: str,
    request: Request,
    session: SessionDep,
) -> BrStatus:
    """Re-run the ingest for a BR to pick up late kills. Gated."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
    br = result.scalar_one_or_none()
    if br is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    settings = get_settings()
    schedule_ingest(settings, br_id)

    return BrStatus(
        br_id=br.br_id,
        status=br.status,
        progress_pct=br.progress_pct,
        error_text=br.error_text,
    )


def _source_to_out(src: BrSource) -> BrSourceOut:
    return BrSourceOut(
        source_id=src.source_id,
        br_id=src.br_id,
        kind=src.kind,
        url=src.url,
        system_id=src.system_id,
        window_start=src.window_start,
        window_end=src.window_end,
        label=src.label,
        status=src.status,
        error_text=src.error_text,
        km_count=src.km_count,
    )


def compute_br_summary(brs: list[BattleReport]) -> BrListSummary:
    wins = sum(1 for b in brs if b.result == "win")
    ties = sum(1 for b in brs if b.result == "tie")
    losses = sum(1 for b in brs if b.result == "loss")
    decided = sum(1 for b in brs if b.result is not None)
    win_rate = wins / decided if decided > 0 else 0.0
    return BrListSummary(
        total=len(brs),
        wins=wins,
        ties=ties,
        losses=losses,
        win_rate=win_rate,
        total_isk_destroyed=sum(b.our_isk_destroyed for b in brs),
        total_isk_lost=sum(b.our_isk_lost for b in brs),
    )


@router.get("/api/brs")
async def list_brs(
    session: SessionDep,
) -> BrListResponse:
    """Return a list of all battle reports with aggregate summary."""
    result = await session.execute(
        select(BattleReport).order_by(
            BattleReport.battle_at.desc().nulls_last(),
            BattleReport.created_at.desc(),
        )
    )
    brs = list(result.scalars())

    summary = compute_br_summary(brs)
    br_list = [_br_to_summary(b) for b in brs]
    return BrListResponse(summary=summary, brs=br_list)


@router.get("/api/brs/{br_id}")
async def get_br(
    br_id: str,
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
) -> list[FightOut]:
    """Return the fights for a battle report."""
    result = await session.execute(
        select(BattleReport.br_id).where(BattleReport.br_id == br_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    return await _load_fights(session, br_id)


@router.get("/api/brs/{br_id}/participants")
async def get_br_participants(
    br_id: str,
    request: Request,
    session: SessionDep,
) -> list[dict]:  # type: ignore[type-arg]
    """Return the union of killmail participants and logged characters for a BR.

    Each entry has: character_id, character_name, user_name, on_killmail, has_logs, fight_ids.
    404 if the BR doesn't exist.  Requires authentication.
    """
    current_user(request)  # auth check
    settings = get_settings()

    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    participants = await br_participants(session, settings, br_id)
    return [_participant_to_dict(p) for p in participants]


def _participant_to_dict(p: ParticipantInfo) -> dict:  # type: ignore[type-arg]
    return {
        "character_id": p.character_id,
        "character_name": p.character_name,
        "user_name": p.user_name,
        "on_killmail": p.on_killmail,
        "has_logs": p.has_logs,
        "fight_ids": p.fight_ids,
    }


@router.get("/api/brs/{br_id}/coverage")
async def get_br_coverage(
    br_id: str,
    request: Request,
    session: SessionDep,
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
    session: SessionDep,
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
            capitals_involved=fight.capitals_involved,
            sides=sides,
        ))

    return out
