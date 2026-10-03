"""FastAPI router for gamelog bulk upload and personal log history.

POST /api/logs     — accepts many files, returns per-file result list.
GET  /api/logs/mine — the caller's uploaded logs, newest first.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.access import acting_user, can_view_character
from app.api.auth import current_user
from app.api.deps import SessionDep, SessionMakerDep
from app.api.derived_cache import bump_derived
from app.config import get_settings
from app.db.models import GamelogFile, LogEvent
from app.fights.offbr_cache import get_offbr_cache
from app.fights.offbr_resolve import resolve_log_characters
from app.logs.associate import associate_file_to_all
from app.logs.extract import build_battle_log
from app.logs.ingest import GamelogFileResult, ingest_log
from app.observability.logging import log
from app.roster.snapshot import get_roster_store

router = APIRouter()


async def _build_roster_lookup() -> Callable[[str], int | None]:
    settings = get_settings()
    try:
        roster = await get_roster_store(settings).get()
        name_to_id = roster.name_to_char_id  # already lowercase
        return lambda name: name_to_id.get(name.strip().lower())
    except Exception as exc:
        log.warning("logs.roster_lookup_failed", error=str(exc))
        return lambda name: None


async def _resolve_counterparties(
    session_maker: async_sessionmaker[AsyncSession], file_ids: list[int]
) -> None:
    """Resolve the uploaded files' counterparty names to characters via ESI and
    persist them, so off-BR participants are identifiable on read.

    Runs AFTER the response, in its own session: the names are read first (no
    write lock), ESI is called, and only then is a short write transaction opened.
    Best-effort — an ESI failure never affects the upload that triggered it.
    """
    settings = get_settings()
    try:
        async with session_maker() as session:
            rows = (
                await session.execute(
                    select(
                        LogEvent.other_name, LogEvent.source_name, LogEvent.target_name
                    )
                    .where(LogEvent.file_id.in_(file_ids))
                    .distinct()
                )
            ).all()
            names = {v for row in rows for v in row if v}
            if not names:
                return
            if await resolve_log_characters(session, settings, names):
                await session.commit()
                # Newly known characters can surface as off-BR participants.
                get_offbr_cache().clear()
                bump_derived()
    except Exception as exc:
        log.warning("logs.upload.resolve_failed", error=str(exc))


@router.post("/api/logs")
async def upload_logs(
    request: Request,
    files: list[UploadFile],
    session_maker: SessionMakerDep,
    background: BackgroundTasks,
) -> list[dict[str, Any]]:
    """Bulk upload gamelog files.  Per-file results; never aborts on one bad file.

    Each file is its own short transaction (parse → insert → associate → commit).
    Counterparty-name resolution needs ESI, so it is deferred to a background task
    that runs after the response — the SQLite write lock is never held across a
    network call.
    """
    user = current_user(request)
    settings = get_settings()
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files in one request (max {settings.max_upload_files})",
        )
    roster_lookup = await _build_roster_lookup()
    max_bytes = settings.max_log_mb * 1024 * 1024

    results: list[dict[str, Any]] = []
    new_file_ids: list[int] = []

    for upload in files:
        filename = upload.filename or "unknown.txt"
        try:
            # Reject by declared size BEFORE reading the body into memory.
            if upload.size is not None and upload.size > max_bytes:
                raise ValueError(
                    f"File too large: {upload.size} bytes exceeds {settings.max_log_mb} MB limit"
                )
            raw_bytes = await upload.read()
            async with session_maker() as session:
                result: GamelogFileResult = await ingest_log(
                    session=session,
                    settings=settings,
                    uploaded_by_user=user.user_name,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    roster_lookup=roster_lookup,
                )
                # Wire-in: associate a resolved upload against all existing fights.
                # associate_file_to_all is guarded; failure is logged, not raised.
                fresh = not result.duplicate and result.parse_status == "parsed"
                if fresh:
                    await associate_file_to_all(session, result.file_id)
                await session.commit()
            if fresh:
                new_file_ids.append(result.file_id)
                # New events can add off-BR participants to any BR they overlap.
                get_offbr_cache().clear()
                bump_derived()

            status = "duplicate" if result.duplicate else result.parse_status
            results.append(
                {
                    "filename": filename,
                    "file_id": result.file_id,
                    "status": status,
                    "event_count": result.event_count,
                    "character_name": result.character_name,
                    "message": None,
                }
            )
        except Exception as exc:
            log.warning("logs.upload.file_error", filename=filename, error=str(exc))
            results.append(
                {
                    "filename": filename,
                    "file_id": None,
                    "status": "error",
                    "event_count": 0,
                    "character_name": None,
                    "message": str(exc),
                }
            )

    if new_file_ids:
        background.add_task(_resolve_counterparties, session_maker, new_file_ids)
    return results


@router.get("/api/logs/mine")
async def get_my_logs(request: Request, session: SessionDep) -> list[dict[str, Any]]:
    """Return the current user's uploaded logs, newest first."""
    user = current_user(request)

    result = await session.execute(
        select(GamelogFile)
        .where(GamelogFile.uploaded_by_user == user.user_name)
        .order_by(GamelogFile.uploaded_at.desc())
    )
    files = list(result.scalars())

    return [
        {
            "file_id": f.file_id,
            "filename": f.original_filename,
            "character_id": f.claimed_character_id,
            "character_name": f.character_name,
            "listener_name": f.listener_name,
            "parse_status": f.parse_status,
            "event_count": f.event_count,
            "log_start_at": f.log_start_at.isoformat() if f.log_start_at else None,
            "log_end_at": f.log_end_at.isoformat() if f.log_end_at else None,
            "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
        }
        for f in files
    ]


@router.get("/api/brs/{br_id}/logs/{character_id}/download")
async def download_character_battle_log(
    br_id: str, character_id: int, request: Request, session: SessionDep
) -> Response:
    """Download a character's gamelog for this battle, sliced to the battle window
    and stripped of EVE/HTML markup. Concatenates if the character has >1 file."""
    user = await acting_user(request)
    if not await can_view_character(user, character_id):
        raise HTTPException(status_code=403, detail="not allowed to view this character")

    result = await build_battle_log(session, br_id, character_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no logs for this character in this battle")

    text, filename = result
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
