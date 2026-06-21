"""Gamelog ingest: dedupe + validate + store + parse + resolve + bulk-insert events.

Public API
----------
    result = await ingest_log(
        session, settings, uploaded_by_user, filename, raw_bytes, roster_lookup
    )

Dedupe contract: sha256 is checked *first*.  If a GamelogFile row with that sha256
already exists, returns immediately with ``duplicate=True`` — no parse, no disk write,
no new events.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.entity import split_entity
from app.logs.filename import parse_filename, resolve_character
from app.logs.parse import parse_log
from app.logs.store import validate_and_store
from app.observability.logging import log
from app.sde.load import entity_name_set


class GamelogFileResult(NamedTuple):
    file_id: int
    duplicate: bool
    parse_status: str
    event_count: int
    character_id: int | None
    character_name: str | None
    listener_name: str | None
    original_filename: str | None


async def ingest_log(
    session: AsyncSession,
    settings: Settings,
    uploaded_by_user: str,
    filename: str,
    raw_bytes: bytes,
    roster_lookup: Callable[[str], int | None],
) -> GamelogFileResult:
    """Parse, resolve, and persist a single gamelog upload.

    Parameters
    ----------
    session:
        An open async SQLAlchemy session (caller is responsible for commit).
    settings:
        App settings (provides log_dir, max_log_mb).
    uploaded_by_user:
        The authenticated user's user_name.
    filename:
        The original filename (used for parse_filename to extract char_id and session start).
    raw_bytes:
        Raw file content.
    roster_lookup:
        Callable(name: str) -> int | None  — maps a listener name to a character_id.
        Build from RosterSnapshot.name_to_char_id:
        ``lambda n: snap.name_to_char_id.get(n.lower())``.

    Returns
    -------
    GamelogFileResult
        ``duplicate=True`` when sha256 already exists in DB (no side effects performed).
    """
    # 1. Dedupe check: compute sha256 before touching disk or DB
    sha = hashlib.sha256(raw_bytes).hexdigest()
    existing = (
        await session.execute(select(GamelogFile).where(GamelogFile.sha256 == sha))
    ).scalar_one_or_none()

    if existing is not None:
        log.info("logs.ingest.duplicate", sha256=sha, file_id=existing.file_id)
        return GamelogFileResult(
            file_id=existing.file_id,
            duplicate=True,
            parse_status=existing.parse_status,
            event_count=existing.event_count,
            character_id=existing.claimed_character_id,
            character_name=existing.character_name,
            listener_name=existing.listener_name,
            original_filename=existing.original_filename,
        )

    # 2. Validate + store (raises ValueError on bad content or oversize)
    store_result = validate_and_store(raw_bytes, settings, sha256=sha)

    # 3. Parse
    text = raw_bytes.decode("utf-8", errors="replace")
    parsed = parse_log(text)

    # 4. Resolve owning character
    filename_meta = parse_filename(filename)
    char_info = resolve_character(filename_meta, parsed.header, roster_lookup)
    character_id: int | None = char_info["character_id"]
    character_name: str | None = char_info["character_name"]
    resolved_via: str = char_info["resolved_via"]

    # 5. Determine parse_status
    parse_status = "unresolved" if character_id is None else "parsed"

    # 6. Derive log time bounds from events
    event_ts_list = [e.ts for e in parsed.events]
    log_start_at = min(event_ts_list) if event_ts_list else None
    log_end_at = max(event_ts_list) if event_ts_list else None

    # 7. Insert GamelogFile row
    now = dt.datetime.now(dt.UTC)
    session_started_at = parsed.header.session_started

    gf = GamelogFile(
        uploaded_by_user=uploaded_by_user,
        claimed_character_id=character_id,
        listener_name=parsed.header.listener_name,
        character_name=character_name,
        original_filename=filename,
        resolved_via=resolved_via,
        session_started_at=session_started_at,
        log_start_at=log_start_at,
        log_end_at=log_end_at,
        stored_path=str(store_result.stored_path),
        sha256=store_result.sha256,
        mime=store_result.mime,
        size=store_result.size,
        parse_status=parse_status,
        event_count=len(parsed.events),
        uploaded_at=now,
    )
    session.add(gf)
    try:
        await session.flush()  # Get file_id assigned before bulk-insert
    except IntegrityError:
        await session.rollback()
        # Re-query the winner row (concurrent upload committed first)
        existing = (
            await session.execute(select(GamelogFile).where(GamelogFile.sha256 == sha))
        ).scalar_one()
        log.info("logs.ingest.race_duplicate", sha256=sha, file_id=existing.file_id)
        return GamelogFileResult(
            file_id=existing.file_id,
            duplicate=True,
            parse_status=existing.parse_status,
            event_count=existing.event_count,
            character_id=existing.claimed_character_id,
            character_name=existing.character_name,
            listener_name=existing.listener_name,
            original_filename=existing.original_filename,
        )

    # Recover Character (Ship) for non-damage targets the parser left merged, using
    # the SDE ship-name dictionary. Damage already splits (ship in parens).
    entity_names = await entity_name_set(session)
    for e in parsed.events:
        if e.effect_type and e.effect_type != "damage" and not e.other_ship_name and e.other_name:
            char, ship = split_entity(e.other_name, entity_names)
            object.__setattr__(e, "other_name", char if char is not None else e.other_name)
            object.__setattr__(e, "other_ship_name", ship)

    # 8. Bulk-insert LogEvent rows
    if parsed.events:
        events = [
            LogEvent(
                file_id=gf.file_id,
                character_id=character_id,
                ts=e.ts,
                direction=e.direction,
                effect_type=e.effect_type,
                amount=e.amount,
                quality=e.quality,
                other_name=e.other_name,
                other_corp_ticker=e.other_corp_ticker,
                other_alliance_ticker=e.other_alliance_ticker,
                other_ship_name=e.other_ship_name,
                module_name=e.module_name,
                source_name=e.source_name,
                target_name=e.target_name,
                authoritative=e.authoritative,
                fight_id=None,
            )
            for e in parsed.events
        ]
        session.add_all(events)

    log.info(
        "logs.ingest.persisted",
        file_id=gf.file_id,
        sha256=sha,
        parse_status=parse_status,
        events=len(parsed.events),
    )

    return GamelogFileResult(
        file_id=gf.file_id,
        duplicate=False,
        parse_status=parse_status,
        event_count=len(parsed.events),
        character_id=character_id,
        character_name=character_name,
        listener_name=parsed.header.listener_name,
        original_filename=filename,
    )
