"""FastAPI router for Battle Report CRUD and status endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import fight_side_losses, load_overrides
from app.api.access import acting_user
from app.api.auth import can_create_br, current_user
from app.api.deps import SessionDep
from app.api.schemas import (
    AttackerDamageRowOut,
    BrCreate,
    BrCreated,
    BrDamageLeaderboardOut,
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
    ItemLossBreakdownOut,
    ItemLossRowOut,
    LeaderboardRowOut,
    LossDamageAttributionOut,
    SlotLossOut,
)
from app.config import get_app_config, get_settings
from app.db.models import (
    BattleReport,
    BrFight,
    BrKillmail,
    BrShipCount,
    BrSideOverride,
    BrSource,
    Fight,
    FightKill,
    Killmail,
    LogEvent,
    LogEventBucket,
    SolarSystem,
)
from app.fights.participants import ParticipantInfo, br_participants
from app.fights.timeline_rows import enrich_br_rows
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


async def _delete_br_cascade(session: AsyncSession, br_id: str) -> None:
    """Delete a BR and all of its scoped data, in FK-dependency order.

    SQLite runs with ``foreign_keys=ON`` and several BR-child FKs have no
    ``ON DELETE CASCADE``, so rows are removed explicitly in order. Shared rows
    are preserved: a Fight or Killmail still referenced by ANOTHER battle report
    is kept, and user-owned raw logs (GamelogFile/LogEvent) are retained — their
    fight stamps are cleared so they can re-associate to a future re-ingest.
    """
    # This BR's fights, and every killmail it references (via the link table and
    # via those fights) — captured before anything is deleted.
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    km_ids: set[int] = set(
        (
            await session.execute(
                select(BrKillmail.killmail_id).where(BrKillmail.br_id == br_id)
            )
        ).scalars()
    )
    if fight_ids:
        km_ids.update(
            (
                await session.execute(
                    select(FightKill.killmail_id).where(FightKill.fight_id.in_(fight_ids))
                )
            ).scalars()
        )

    # Detach user-owned logs from this BR's fights (keep the raw uploads) and drop
    # the derived per-fight buckets.
    if fight_ids:
        await session.execute(
            update(LogEvent).where(LogEvent.fight_id.in_(fight_ids)).values(fight_id=None)
        )
        await session.execute(
            delete(LogEventBucket).where(LogEventBucket.fight_id.in_(fight_ids))
        )

    # BR-scoped tables.
    await session.execute(delete(BrSideOverride).where(BrSideOverride.br_id == br_id))
    await session.execute(delete(BrShipCount).where(BrShipCount.br_id == br_id))
    await session.execute(delete(BrSource).where(BrSource.br_id == br_id))
    await session.execute(delete(BrKillmail).where(BrKillmail.br_id == br_id))
    await session.execute(delete(BrFight).where(BrFight.br_id == br_id))

    # Fights no longer referenced by any BR → delete (cascades FightSide / FightKill
    # / FightShipCount via their ON DELETE CASCADE on fight_id).
    if fight_ids:
        still_fights = set(
            (
                await session.execute(
                    select(BrFight.fight_id).where(BrFight.fight_id.in_(fight_ids))
                )
            ).scalars()
        )
        orphan_fights = [f for f in fight_ids if f not in still_fights]
        if orphan_fights:
            await session.execute(delete(Fight).where(Fight.fight_id.in_(orphan_fights)))

    # Killmails no longer referenced by any BR link or fight → delete (cascades
    # KillmailAttacker / KillmailItem).
    if km_ids:
        km_list = list(km_ids)
        ref_brkm = set(
            (
                await session.execute(
                    select(BrKillmail.killmail_id).where(BrKillmail.killmail_id.in_(km_list))
                )
            ).scalars()
        )
        ref_fk = set(
            (
                await session.execute(
                    select(FightKill.killmail_id).where(FightKill.killmail_id.in_(km_list))
                )
            ).scalars()
        )
        orphan_km = [k for k in km_list if k not in ref_brkm and k not in ref_fk]
        if orphan_km:
            await session.execute(delete(Killmail).where(Killmail.killmail_id.in_(orphan_km)))

    # Finally the BR row itself.
    await session.execute(delete(BattleReport).where(BattleReport.br_id == br_id))


@router.delete("/api/brs/{br_id}", status_code=204)
async def delete_br(
    br_id: str,
    request: Request,
    session: SessionDep,
) -> None:
    """Delete a battle report and its scoped data. FC / High Command only."""
    user = current_user(request)
    if not can_create_br(user):
        raise HTTPException(status_code=403, detail="Forbidden")

    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    await _delete_br_cascade(session, br_id)
    await session.commit()
    log.info("brs.deleted", br_id=br_id, user=user.user_name)


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


async def enrich_summaries(
    session: AsyncSession,
    request: Request,
    summaries: list[BrSummary],
) -> list[BrSummary]:
    """Attach timeline-list extras (sides, pilots, presence, log coverage) to summaries."""
    if not summaries:
        return summaries
    # acting_user honours DEV_MODE impersonation, so "present" / "your logs"
    # reflect the user being viewed — matching /api/me.
    user = await acting_user(request)
    cfg = get_app_config()
    settings = get_settings()
    extras = await enrich_br_rows(
        session,
        settings,
        [s.br_id for s in summaries],
        user_name=user.user_name,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
    )
    out: list[BrSummary] = []
    for s in summaries:
        extra = extras.get(s.br_id)
        out.append(s.model_copy(update=vars(extra)) if extra is not None else s)
    return out


@router.get("/api/brs")
async def list_brs(
    session: SessionDep,
    request: Request,
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
    br_list = await enrich_summaries(session, request, [_br_to_summary(b) for b in brs])
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

    sys_ids = [f.system_id for f in fights]
    system_names: list[str] = []
    if sys_ids:
        name_map = {
            s.system_id: s.name
            for s in (
                await session.execute(
                    select(SolarSystem).where(SolarSystem.system_id.in_(sys_ids))
                )
            ).scalars()
        }
        seen: set[str] = set()
        for sid in sys_ids:
            nm = name_map.get(sid) or f"System {sid}"
            if nm not in seen:
                seen.add(nm)
                system_names.append(nm)

    return BrDetail(
        **_br_to_summary(br).model_dump(exclude={"systems"}),
        fights=fights,
        systems=system_names,
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


@router.get("/api/brs/{br_id}/losses/{killmail_id}/damage")
async def get_loss_damage_attribution(
    br_id: str,
    killmail_id: int,
    session: SessionDep,
) -> LossDamageAttributionOut:
    """Return per-attacker damage attribution for one killmail in a battle report.

    404 if the killmail is not linked to the given BR via the BR→fight→FightKill chain.
    """
    from app.analytics.damage_attribution import loss_damage_attribution

    # Guard: verify killmail belongs to this BR via BrFight → FightKill join.
    km_in_br = (
        await session.execute(
            select(FightKill.killmail_id)
            .join(BrFight, BrFight.fight_id == FightKill.fight_id)
            .where(BrFight.br_id == br_id, FightKill.killmail_id == killmail_id)
        )
    ).scalar_one_or_none()
    if km_in_br is None:
        raise HTTPException(status_code=404, detail="Killmail not found in this battle report")

    result = await loss_damage_attribution(session, killmail_id)
    return LossDamageAttributionOut(
        killmail_id=result.killmail_id,
        damage_taken=result.damage_taken,
        total_attributed=result.total_attributed,
        attackers=[
            AttackerDamageRowOut(
                character_id=a.character_id,
                character_name=a.character_name,
                damage_done=a.damage_done,
                share=a.share,
                final_blow=a.final_blow,
            )
            for a in result.attackers
        ],
    )


@router.get("/api/brs/{br_id}/losses/{killmail_id}/items")
async def get_loss_item_breakdown(
    br_id: str,
    killmail_id: int,
    session: SessionDep,
) -> ItemLossBreakdownOut:
    """Return per-slot item loss breakdown for one killmail in a battle report.

    Groups KillmailItem rows by slot category with destroyed/dropped quantity
    sums and individual item rows (names resolved from InventoryType).
    value is always None — no per-item ISK price source is available.

    404 if the killmail is not linked to the given BR via the BR→fight→FightKill chain.
    """
    from app.analytics.item_losses import item_loss_breakdown

    # Guard: verify killmail belongs to this BR via BrFight → FightKill join.
    km_in_br = (
        await session.execute(
            select(FightKill.killmail_id)
            .join(BrFight, BrFight.fight_id == FightKill.fight_id)
            .where(BrFight.br_id == br_id, FightKill.killmail_id == killmail_id)
        )
    ).scalar_one_or_none()
    if km_in_br is None:
        raise HTTPException(status_code=404, detail="Killmail not found in this battle report")

    result = await item_loss_breakdown(session, killmail_id)
    return ItemLossBreakdownOut(
        killmail_id=result.killmail_id,
        slots=[
            SlotLossOut(
                location=sl.location,
                destroyed_qty=sl.destroyed_qty,
                dropped_qty=sl.dropped_qty,
                value=sl.value,
                items=[
                    ItemLossRowOut(
                        type_id=item.type_id,
                        name=item.name,
                        location=item.location,
                        qty_destroyed=item.qty_destroyed,
                        qty_dropped=item.qty_dropped,
                    )
                    for item in sl.items
                ],
            )
            for sl in result.slots
        ],
    )


@router.get("/api/brs/{br_id}/damage-leaderboard")
async def get_br_damage_leaderboard(
    br_id: str,
    session: SessionDep,
) -> BrDamageLeaderboardOut:
    """Return battle-level damage leaderboard for a battle report.

    Sums KillmailAttacker.damage_done per character across all kills in the BR,
    sorted descending. 404 if the BR does not exist.
    log_damage_out is None and logs_present is False (Task 21 wires the overlay).
    """
    from app.analytics.damage_attribution import br_damage_leaderboard

    exists = (
        await session.execute(select(BattleReport.br_id).where(BattleReport.br_id == br_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Battle report not found")

    result = await br_damage_leaderboard(session, br_id)
    return BrDamageLeaderboardOut(
        rows=[
            LeaderboardRowOut(
                character_id=r.character_id,
                character_name=r.character_name,
                damage_done=r.damage_done,
                share=r.share,
                log_damage_out=r.log_damage_out,
            )
            for r in result.rows
        ],
        total_attributed=result.total_attributed,
        logs_present=result.logs_present,
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
    user = await acting_user(request)
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

    # Sides reflect the per-entity classification (baseline blues + FC/HC
    # overrides), NOT the unreliable 2-colouring.
    cfg = get_app_config()
    overrides = await load_overrides(session, br_id)
    losses_by_fight = await fight_side_losses(
        session,
        fight_ids,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
        overrides=overrides,
    )
    _side_order = {"friendly": 0, "hostile": 1, "unassigned": 2}

    out: list[FightOut] = []
    for bf in br_fights:
        fight = fights_by_id.get(bf.fight_id)
        if fight is None:
            continue
        side_map = losses_by_fight.get(fight.fight_id, {})
        sides = [
            FightSideOut(
                side_idx=_side_order.get(name, 9),
                side_kind=name,
                pilot_count=agg["pilots"],
                isk_lost=agg["isk_lost"],
                losses=agg["losses"],
            )
            for name, agg in sorted(
                side_map.items(), key=lambda kv: _side_order.get(kv[0], 9)
            )
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
