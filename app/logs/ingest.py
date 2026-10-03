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

import asyncio
import datetime as dt
import hashlib
from collections.abc import Callable
from typing import NamedTuple

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.entity import correct_ship_pilot_swap, split_entity
from app.logs.filename import parse_filename, resolve_character
from app.logs.parse import ParsedLogEvent, parse_log
from app.logs.store import store_gamelog, validate_gamelog
from app.observability.logging import log
from app.sde.load import entity_name_set

#: parse_status of a log that contains no combat effects at all (a login-screen
#: session, a hauling trip). Nothing is stored for it — not on disk, not in the DB.
EMPTY = "empty"


class GamelogFileResult(NamedTuple):
    #: None when nothing was stored (parse_status == EMPTY).
    file_id: int | None
    duplicate: bool
    parse_status: str
    event_count: int
    character_id: int | None
    character_name: str | None
    listener_name: str | None
    original_filename: str | None


_TACKLE: frozenset[str] = frozenset({"scram", "disrupt"})


def build_event_rows(
    events: list[ParsedLogEvent],
    entity_names: frozenset[str],
    *,
    file_id: int,
    character_id: int | None,
    character_name: str | None,
) -> list[dict[str, object]]:
    """Turn parsed events into LogEvent insert rows — the ONE place parsed lines are
    cleaned for storage, shared by upload (ingest_log) and reparse so a parser fix
    can never apply to one path and not the other.

    Only lines that parsed to an effect are stored. Envelope lines with no effect
    (hints, misses, questions) carry nothing a reader uses; they are counted in the
    file's parser stats (GamelogFile.combat_lines / unmatched_combat) instead.
    """
    rows: list[dict[str, object]] = []
    for e in events:
        if e.effect_type is None:
            continue
        other_name, other_ship = e.other_name, e.other_ship_name
        if e.effect_type != "damage" and not other_ship and other_name:
            # Recover Character (Ship) for non-damage targets the parser left merged,
            # using the SDE ship-name dictionary. Damage already splits (ship in parens).
            char, ship = split_entity(other_name, entity_names)
            # Ship-only / NPC counterparty (char None, ship found) → name must become
            # None, NOT fall back to the raw ship string (else other_name == ship). Only
            # keep the original when split_entity recovered nothing at all.
            other_name = char if (char is not None or ship is not None) else other_name
            other_ship = ship
        elif e.effect_type != "damage" and other_ship and other_name:
            # Correct the rare ship-first "Ship [CORP] Pilot" overview the parser
            # assumed was NEW (pilot-first) and assigned backwards.
            other_name, other_ship = correct_ship_pilot_swap(other_name, other_ship, entity_names)
        # Clean source_name/target_name for EWAR lines: the parser emits raw strings
        # like "Proteus Nate Marston [NVACA] <NV>" — split_entity strips the ship prefix
        # and tickers to leave just the character (None when ship-only / NPC).
        # Only run the SDE ship-peel when the parser did NOT already separate a ship
        # from the pilot. When it did (the "[bracket] pilot" overview, *_ship_name set),
        # the name is already a clean pilot; re-splitting would wrongly peel a pilot
        # whose FIRST NAME is itself a ship hull (e.g. "Wolf Hibra" → "Hibra").
        source_name, target_name = e.source_name, e.target_name
        if source_name and e.source_ship_name is None:
            source_name, _ = split_entity(source_name, entity_names)
        if target_name and e.target_ship_name is None:
            target_name, _ = split_entity(target_name, entity_names)
        # Resolve "you" to the owner for authoritative tackle events. Fill ONLY the
        # party that is actually the log owner (source_is_you / target_is_you), never
        # any None side — the other party is None when it is an unresolved ship-only
        # counterparty, and filling it with the owner fabricates a self-tackle.
        if e.effect_type in _TACKLE and character_name is not None:
            if e.source_is_you:
                source_name = character_name
            if e.target_is_you:
                target_name = character_name
        rows.append(
            dict(
                file_id=file_id,
                character_id=character_id,
                ts=e.ts,
                direction=e.direction,
                effect_type=e.effect_type,
                amount=e.amount,
                quality=e.quality,
                other_name=other_name,
                other_corp_ticker=e.other_corp_ticker,
                other_alliance_ticker=e.other_alliance_ticker,
                other_ship_name=other_ship,
                module_name=e.module_name,
                source_name=source_name,
                target_name=target_name,
                authoritative=e.authoritative,
                dedupe_suppressed=False,
                fight_id=None,
            )
        )
    return rows


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

    # 2. Validate (raises ValueError on bad content or oversize)
    validate_gamelog(raw_bytes, settings)

    # 3. Parse — CPU-bound (regex over every line of a multi-MB file), so run it
    # in a worker thread instead of stalling every other request on the event loop.
    text = raw_bytes.decode("utf-8", errors="replace")
    parsed = await asyncio.to_thread(parse_log, text)

    # A log with no combat effects has nothing to contribute: report it as such and
    # store nothing, rather than keeping a row that reads as a failed upload.
    if not any(e.effect_type is not None for e in parsed.events):
        log.info("logs.ingest.empty", sha256=sha)
        return GamelogFileResult(
            file_id=None,
            duplicate=False,
            parse_status=EMPTY,
            event_count=0,
            character_id=None,
            character_name=parsed.header.listener_name,
            listener_name=parsed.header.listener_name,
            original_filename=filename,
        )

    store_result = store_gamelog(raw_bytes, settings, sha256=sha)

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
        event_count=0,  # set below to the number of stored (effect) events
        combat_lines=parsed.stats["combat_lines"],
        unmatched_combat=parsed.stats["unmatched_combat"],
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

    # 8. Bulk-insert LogEvent rows (effect lines only; see build_event_rows).
    entity_names = await entity_name_set(session)
    rows = build_event_rows(
        parsed.events,
        entity_names,
        file_id=gf.file_id,
        character_id=character_id,
        character_name=character_name,
    )
    if rows:
        await session.execute(insert(LogEvent), rows)
    gf.event_count = len(rows)

    log.info(
        "logs.ingest.persisted",
        file_id=gf.file_id,
        sha256=sha,
        parse_status=parse_status,
        events=len(rows),
    )

    return GamelogFileResult(
        file_id=gf.file_id,
        duplicate=False,
        parse_status=parse_status,
        event_count=len(rows),
        character_id=character_id,
        character_name=character_name,
        listener_name=parsed.header.listener_name,
        original_filename=filename,
    )
