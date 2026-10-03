"""FastAPI router for fleet-broadcast upload + analytics.

Upload/delete are gated on ``can_create_br`` (FC / High Command) — the same
elevated check used for BR create/edit/delete.  The read endpoints (metrics, raw
markers, current file) are visible to all authenticated users, like the fleet timeline.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile
from sqlalchemy import select

from app.analytics.broadcasts import (
    BroadcastMetrics,
    compute_broadcast_metrics,
)
from app.analytics.performance import BrPerformance, compute_br_performance
from app.api.access import acting_user, viewer_scope
from app.api.auth import CurrentUser, can_create_br
from app.api.deps import SessionDep
from app.api.fleet import _require_br
from app.api.logs import _build_roster_lookup
from app.api.schemas import (
    BroadcastFileOut,
    BroadcastMetricsOut,
    BroadcastRawItemOut,
    BroadcastSummaryOut,
    BroadcastUploadResult,
    BrPerformanceOut,
    DeathBroadcastRowOut,
    FleetDistributionsOut,
    PerfCharRowOut,
    PilotSwitchRowOut,
    QualityReportOut,
    RepRequestMetricsOut,
    RepRequestRowOut,
    TargetCallMetricsOut,
    TargetCallRowOut,
)
from app.config import get_app_config, get_settings
from app.db.models import Broadcast, BroadcastFile
from app.logs.broadcast_ingest import delete_broadcast_file, ingest_broadcast
from app.observability.logging import log
from app.roster.snapshot import get_roster_store

router = APIRouter()


async def _require_elevated(request: Request) -> None:
    acting = await acting_user(request, get_settings())
    if not can_create_br(acting):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _viewer_character_ids(acting: CurrentUser) -> set[int]:
    """The character ids the acting user owns (their own alts), for self-view filtering."""
    ids: set[int] = set()
    if acting.main_character_id and acting.main_character_id.isdigit():
        ids.add(int(acting.main_character_id))
    try:
        roster = await get_roster_store(get_settings()).get()
    except Exception:
        return ids
    for c in roster.user_to_chars.get(acting.user_name, []):
        ids.add(c.character_id)
    return ids


def _metrics_out(m: BroadcastMetrics, elevated: bool) -> BroadcastMetricsOut:
    """Serialise broadcast metrics. Named per-character rows (per_pilot, deaths, and
    friendly rep rows) are FC/HC-only; the aggregate summary + enemy-named target rows
    stay public."""
    return BroadcastMetricsOut(
        has_broadcasts=m.has_broadcasts,
        summary=BroadcastSummaryOut(**m.summary.__dict__),
        targets=TargetCallMetricsOut(
            rows=[TargetCallRowOut(**r.__dict__) for r in m.targets.rows],
            per_pilot=(
                [PilotSwitchRowOut(**p.__dict__) for p in m.targets.per_pilot]
                if elevated
                else []
            ),
            compliance_rate=m.targets.compliance_rate,
            unanswered_count=m.targets.unanswered_count,
        ),
        reps=RepRequestMetricsOut(
            rows=(
                [RepRequestRowOut(**r.__dict__) for r in m.reps.rows] if elevated else []
            ),
            false_broadcast_rate=m.reps.false_broadcast_rate,
            median_logi_response_s=m.reps.median_logi_response_s,
            median_damage_lead_s=m.reps.median_damage_lead_s,
            late_broadcast_rate=m.reps.late_broadcast_rate,
            unresolved_subjects=m.reps.unresolved_subjects,
        ),
        quality=QualityReportOut(**m.quality.__dict__),
        deaths=[DeathBroadcastRowOut(**d.__dict__) for d in m.deaths] if elevated else [],
    )


def _performance_out(p: BrPerformance) -> BrPerformanceOut:
    return BrPerformanceOut(
        elevated=p.elevated,
        has_broadcasts=p.has_broadcasts,
        summary=BroadcastSummaryOut(**p.summary.__dict__),
        distributions=FleetDistributionsOut(**p.distributions.__dict__),
        characters=[PerfCharRowOut(**c.__dict__) for c in p.characters],
    )


@router.post("/api/brs/{br_id}/broadcasts")
async def upload_broadcast(
    br_id: str, file: UploadFile, request: Request, session: SessionDep
) -> BroadcastUploadResult:
    """Upload a fleet-broadcast log for *br_id*.  FC / High Command only.

    Re-uploading an extended log auto-supersedes the prior file; combine with DELETE
    to fully replace a wrong upload.
    """
    await _require_br(br_id, session)
    await _require_elevated(request)
    acting = await acting_user(request, get_settings())
    roster_lookup = await _build_roster_lookup()
    raw_bytes = await file.read()
    try:
        result = await ingest_broadcast(
            session=session,
            settings=get_settings(),
            br_id=br_id,
            uploaded_by_user=acting.user_name,
            filename=file.filename or "broadcast.txt",
            raw_bytes=raw_bytes,
            roster_lookup=roster_lookup,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return BroadcastUploadResult(
        file_id=result.file_id,
        status="duplicate" if result.duplicate else result.parse_status,
        broadcast_count=result.broadcast_count,
        br_id=result.br_id,
    )


@router.get("/api/brs/{br_id}/broadcasts/metrics")
async def get_broadcast_metrics(
    br_id: str, request: Request, session: SessionDep
) -> BroadcastMetricsOut:
    """Fleet-broadcast metrics for *br_id*.

    The aggregate summary is public; per-character named rows (per-pilot discipline,
    named rep requests, flagged deaths) are returned only to FC / High Command.
    """
    await _require_br(br_id, session)
    acting = await acting_user(request, get_settings())
    return _metrics_out(
        await compute_broadcast_metrics(session, br_id), elevated=can_create_br(acting)
    )


@router.get("/api/brs/{br_id}/performance")
async def get_performance(
    br_id: str, request: Request, session: SessionDep
) -> BrPerformanceOut:
    """Per-character battle performance.

    Everyone sees the anonymous fleet distributions + their own characters' rows; FC /
    High Command additionally see every friendly pilot's row.
    """
    await _require_br(br_id, session)
    settings = get_settings()
    acting = await acting_user(request, settings)
    char_to_user: dict[int, str] | None = None
    try:
        roster = await get_roster_store(settings).get()
        char_to_user = dict(roster.char_to_user)
    except Exception:  # roster unavailable → rows carry no main/user mapping
        char_to_user = None
    perf = await compute_br_performance(
        session,
        br_id,
        viewer_character_ids=await _viewer_character_ids(acting),
        elevated=can_create_br(acting),
        cfg=get_app_config(),
        settings=settings,
        char_to_user=char_to_user,
    )
    return _performance_out(perf)


@router.get("/api/brs/{br_id}/broadcasts")
async def list_broadcasts(
    br_id: str, request: Request, session: SessionDep
) -> list[BroadcastRawItemOut]:
    """Raw broadcast markers for the timeline overlay (canonical file only).

    Target calls name enemies and are public. Rep/cap requests name a friendly
    pilot, so non-elevated viewers only get the ones about their own characters.
    """
    await _require_br(br_id, session)
    viewer = await viewer_scope(request, get_settings())
    rows = (
        await session.execute(
            select(
                Broadcast.broadcast_id,
                Broadcast.ts,
                Broadcast.kind,
                Broadcast.subject_name,
                Broadcast.subject_ship,
                Broadcast.fight_id,
                Broadcast.subject_character_id,
            )
            .join(BroadcastFile, BroadcastFile.broadcast_file_id == Broadcast.file_id)
            .where(Broadcast.br_id == br_id)
            .where(BroadcastFile.superseded.is_(False))
            .order_by(Broadcast.ts)
        )
    ).all()
    return [
        BroadcastRawItemOut(
            broadcast_id=bid,
            ts=ts,
            kind=kind,
            subject_name=sname,
            subject_ship=sship,
            fight_id=fid,
        )
        for (bid, ts, kind, sname, sship, fid, scid) in rows
        if kind == "target" or viewer.can_see(scid) or viewer.can_see_name(sname)
    ]


@router.get("/api/brs/{br_id}/broadcasts/file")
async def get_broadcast_file(br_id: str, session: SessionDep) -> BroadcastFileOut | None:
    """The canonical broadcast file attached to *br_id*, or null if none."""
    await _require_br(br_id, session)
    bf = (
        await session.execute(
            select(BroadcastFile)
            .where(BroadcastFile.br_id == br_id)
            .where(BroadcastFile.superseded.is_(False))
            .order_by(BroadcastFile.uploaded_at.desc())
        )
    ).scalars().first()
    if bf is None:
        return None
    return BroadcastFileOut(
        file_id=bf.broadcast_file_id,
        original_filename=bf.original_filename,
        broadcast_count=bf.broadcast_count,
        uploaded_by_user=bf.uploaded_by_user,
        uploaded_at=bf.uploaded_at,
    )


@router.delete("/api/brs/{br_id}/broadcasts/{file_id}")
async def delete_broadcast(
    br_id: str, file_id: int, request: Request, session: SessionDep
) -> dict[str, bool]:
    """Delete a broadcast file (and its rows) for *br_id*.  FC / High Command only."""
    await _require_br(br_id, session)
    await _require_elevated(request)
    deleted_br = await delete_broadcast_file(session, file_id)
    if deleted_br is None or deleted_br != br_id:
        raise HTTPException(status_code=404, detail="Broadcast file not found")
    await session.commit()
    log.info("broadcast.api.deleted", br_id=br_id, file_id=file_id)
    return {"ok": True}
