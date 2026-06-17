"""FastAPI router for gamelog bulk upload and personal log history.

POST /api/logs     — accepts many files, returns per-file result list.
GET  /api/logs/mine — the caller's uploaded logs, newest first.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from sqlalchemy import select

from app.api.auth import current_user
from app.api.deps import SessionDep, SessionMakerDep
from app.config import get_settings
from app.db.models import GamelogFile
from app.logs.associate import associate_file_to_all
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


@router.post("/api/logs")
async def upload_logs(
    request: Request,
    files: list[UploadFile],
    session_maker: SessionMakerDep,
) -> list[dict[str, Any]]:
    """Bulk upload gamelog files.  Per-file results; never aborts on one bad file."""
    user = current_user(request)
    settings = get_settings()
    roster_lookup = await _build_roster_lookup()

    results: list[dict[str, Any]] = []

    for upload in files:
        filename = upload.filename or "unknown.txt"
        try:
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
                if not result.duplicate and result.parse_status == "parsed":
                    await associate_file_to_all(session, result.file_id)
                await session.commit()

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
