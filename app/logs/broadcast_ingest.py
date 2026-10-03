"""Fleet-broadcast ingest: dedupe + validate + store + parse + date-reconstruct + persist.

Public API::

    result = await ingest_broadcast(
        session, settings, br_id, uploaded_by_user, filename, raw_bytes, roster_lookup
    )

Unlike gamelogs, broadcasts are ownerless and dateless, and are associated to a
specific ``br_id`` supplied by the caller (an elevated user picks the BR).  Absolute
UTC dates are reconstructed against the BR's fight window (broadcast_parse), then the
rows are stamped to fights by time-window overlap (broadcast_associate).

Dedupe contract mirrors ingest_log: sha256 is checked first; an identical file returns
``duplicate=True`` with no side effects.  Because a growing broadcast log is re-uploaded
with different bytes, a second **supersession** mechanism keyed on ``br_id`` keeps only
the most complete file contributing to analytics (older siblings flagged ``superseded``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    BattleReport,
    BrFight,
    Broadcast,
    BroadcastFile,
    Character,
    Fight,
)
from app.logs.broadcast_associate import associate_broadcasts_for_br
from app.logs.broadcast_parse import (
    ParsedBroadcast,
    choose_anchor_date,
    parse_broadcast,
    reconstruct_dates,
)
from app.observability.logging import log
from app.timeutil import naive_utc as _as_naive_utc

#: Broadcast kinds whose subject is a friendly fleet member (resolvable to a character).
_FRIENDLY_KINDS = frozenset({"needs_shield", "needs_armor", "needs_capacitor", "repair"})


class BroadcastFileResult(NamedTuple):
    file_id: int
    duplicate: bool
    parse_status: str
    broadcast_count: int
    br_id: str
    original_filename: str | None


def _dedupe_key(br_id: str, content_sha: str) -> str:
    """Value stored in ``BroadcastFile.sha256``: unique per (BR, content).

    The column is globally unique, but the same fleet-broadcast log legitimately
    belongs to every BR that covers that fight. Scoping the key by ``br_id`` lets
    one file be attached to several reports while a re-upload to the SAME report is
    still recognised as a duplicate. (Rows written before this carry the bare
    content hash; ``_find_existing`` checks both.) The file on disk stays
    content-addressed by the plain hash.
    """
    return hashlib.sha256(f"{br_id}:{content_sha}".encode()).hexdigest()


async def _find_existing(
    session: AsyncSession, br_id: str, content_sha: str
) -> BroadcastFile | None:
    return (
        await session.execute(
            select(BroadcastFile).where(
                BroadcastFile.br_id == br_id,
                BroadcastFile.sha256.in_([_dedupe_key(br_id, content_sha), content_sha]),
            )
        )
    ).scalars().first()


async def unlink_if_unreferenced(session: AsyncSession, stored_path: str) -> None:
    """Remove a stored broadcast file once no BroadcastFile row points at it."""
    still = (
        await session.execute(
            select(func.count()).select_from(BroadcastFile).where(
                BroadcastFile.stored_path == stored_path
            )
        )
    ).scalar_one()
    if still:
        return
    try:
        Path(stored_path).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("broadcast.unlink_failed", path=stored_path, error=str(exc))


async def _br_reference_end(session: AsyncSession, br_id: str) -> dt.datetime | None:
    """Latest UTC instant to anchor broadcast dates against: the BR's last fight end,
    falling back to the BR's ``battle_at`` when no fights exist yet."""
    fight_end = (
        await session.execute(
            select(func.max(Fight.ended_at))
            .join(BrFight, BrFight.fight_id == Fight.fight_id)
            .where(BrFight.br_id == br_id)
        )
    ).scalar_one_or_none()
    if fight_end is not None:
        return _as_naive_utc(fight_end)
    battle_at = (
        await session.execute(
            select(BattleReport.battle_at).where(BattleReport.br_id == br_id)
        )
    ).scalar_one_or_none()
    return _as_naive_utc(battle_at)


async def _resolve_subject_ids(
    session: AsyncSession,
    names: set[str],
    roster_lookup: Callable[[str], int | None],
) -> dict[str, int]:
    """Map friendly subject names (lowercased) to character_id via roster then Character."""
    resolved: dict[str, int] = {}
    unresolved: set[str] = set()
    for name in names:
        cid = roster_lookup(name)
        if cid is not None:
            resolved[name.lower()] = cid
        else:
            unresolved.add(name)
    if unresolved:
        # Case-insensitive Character.name fallback for anyone not in the roster snapshot.
        rows = (
            await session.execute(
                select(Character.character_id, Character.name).where(
                    func.lower(Character.name).in_({n.lower() for n in unresolved})
                )
            )
        ).all()
        for cid, cname in rows:
            if cname is not None:
                resolved[cname.lower()] = cid
    return resolved


def _canonical_key(bf: BroadcastFile) -> tuple[int, dt.datetime, int]:
    """Higher tuple wins: most broadcasts, then latest end, then newest row."""
    return (
        bf.broadcast_count,
        bf.log_end_at or dt.datetime.min,
        bf.broadcast_file_id,
    )


async def _apply_supersession(session: AsyncSession, br_id: str) -> None:
    """Keep the single most-complete broadcast file per BR; flag the rest superseded."""
    files = list(
        (
            await session.execute(
                select(BroadcastFile).where(BroadcastFile.br_id == br_id)
            )
        ).scalars()
    )
    parsed = [f for f in files if f.parse_status == "parsed"]
    if not parsed:
        return
    canonical = max(parsed, key=_canonical_key)
    for f in files:
        f.superseded = f.broadcast_file_id != canonical.broadcast_file_id
    await session.flush()


async def ingest_broadcast(
    session: AsyncSession,
    settings: Settings,
    br_id: str,
    uploaded_by_user: str,
    filename: str,
    raw_bytes: bytes,
    roster_lookup: Callable[[str], int | None],
) -> BroadcastFileResult:
    """Parse, date-reconstruct, and persist a single fleet-broadcast upload for *br_id*.

    Caller owns the transaction (commit/rollback).  Raises ``ValueError`` on invalid
    content, oversize, or a BR with no fight/battle time to anchor dates against.
    """
    from app.logs.store import validate_and_store_broadcast

    # 1. Dedupe on (BR, content) before any disk/DB writes.
    sha = hashlib.sha256(raw_bytes).hexdigest()
    existing = await _find_existing(session, br_id, sha)
    if existing is not None:
        log.info("broadcast.ingest.duplicate", sha256=sha, file_id=existing.broadcast_file_id)
        return BroadcastFileResult(
            file_id=existing.broadcast_file_id,
            duplicate=True,
            parse_status=existing.parse_status,
            broadcast_count=existing.broadcast_count,
            br_id=existing.br_id,
            original_filename=existing.original_filename,
        )

    # 2. Reference instant to anchor dates (last fight end, else battle_at).
    reference_end = await _br_reference_end(session, br_id)
    if reference_end is None:
        raise ValueError(
            "cannot ingest broadcast: BR has no fights or battle time to anchor dates"
        )

    # 3. Validate + store (raises on bad content/oversize).
    store_result = validate_and_store_broadcast(raw_bytes, settings, sha256=sha)

    # 4. Parse + reconstruct absolute dates.
    text = raw_bytes.decode("utf-8", errors="replace")
    parsed = parse_broadcast(text)
    broadcasts: list[ParsedBroadcast] = parsed.broadcasts
    anchor_date = (
        choose_anchor_date(broadcasts[0].tod, reference_end) if broadcasts else None
    )
    ts_list = reconstruct_dates(broadcasts, anchor_date) if anchor_date else []

    # 5. Resolve friendly subjects to character ids.
    friendly_names = {b.subject_name for b in broadcasts if b.kind in _FRIENDLY_KINDS}
    subject_ids = await _resolve_subject_ids(session, friendly_names, roster_lookup)

    # 6. Insert the BroadcastFile row.
    now = dt.datetime.now(dt.UTC)
    bf = BroadcastFile(
        br_id=br_id,
        uploaded_by_user=uploaded_by_user,
        original_filename=filename,
        stored_path=str(store_result.stored_path),
        sha256=_dedupe_key(br_id, sha),
        mime=store_result.mime,
        size=store_result.size,
        parse_status="parsed",
        broadcast_count=len(broadcasts),
        anchor_date=anchor_date,
        log_start_at=min(ts_list) if ts_list else None,
        log_end_at=max(ts_list) if ts_list else None,
        superseded=False,
        uploaded_at=now,
    )
    session.add(bf)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await _find_existing(session, br_id, sha)
        if existing is None:
            raise
        return BroadcastFileResult(
            file_id=existing.broadcast_file_id,
            duplicate=True,
            parse_status=existing.parse_status,
            broadcast_count=existing.broadcast_count,
            br_id=existing.br_id,
            original_filename=existing.original_filename,
        )

    # 7. Bulk-insert Broadcast rows (Core executemany, like reparse.py).
    rows: list[dict[str, object]] = []
    for b, ts in zip(broadcasts, ts_list, strict=False):
        rows.append(
            dict(
                file_id=bf.broadcast_file_id,
                br_id=br_id,
                fight_id=None,
                ts=ts,
                kind=b.kind,
                subject_name=b.subject_name,
                subject_ship=b.subject_ship,
                subject_character_id=(
                    subject_ids.get(b.subject_name.lower())
                    if b.kind in _FRIENDLY_KINDS
                    else None
                ),
                seq=b.seq,
                raw_line=b.raw,
            )
        )
    if rows:
        await session.execute(insert(Broadcast), rows)
    await session.flush()

    # 8. Supersede less-complete siblings, then stamp fights.
    await _apply_supersession(session, br_id)
    await associate_broadcasts_for_br(session, br_id)

    log.info(
        "broadcast.ingest.persisted",
        file_id=bf.broadcast_file_id,
        br_id=br_id,
        sha256=sha,
        broadcasts=len(rows),
    )
    return BroadcastFileResult(
        file_id=bf.broadcast_file_id,
        duplicate=False,
        parse_status="parsed",
        broadcast_count=len(rows),
        br_id=br_id,
        original_filename=filename,
    )


async def delete_broadcast_file(session: AsyncSession, file_id: int) -> str | None:
    """Delete a broadcast file (CASCADE removes its rows); re-canonicalise its BR.

    Returns the ``br_id`` the file belonged to, or None if it did not exist.
    """
    bf = (
        await session.execute(
            select(BroadcastFile).where(BroadcastFile.broadcast_file_id == file_id)
        )
    ).scalar_one_or_none()
    if bf is None:
        return None
    br_id = bf.br_id
    stored_path = bf.stored_path
    # Explicitly clear child rows (SQLite FK cascade is not always enabled), then the file.
    await session.execute(delete(Broadcast).where(Broadcast.file_id == file_id))
    await session.execute(
        delete(BroadcastFile).where(BroadcastFile.broadcast_file_id == file_id)
    )
    await session.flush()
    await unlink_if_unreferenced(session, stored_path)
    # A previously-superseded sibling may now be the most complete file.
    await _apply_supersession(session, br_id)
    await associate_broadcasts_for_br(session, br_id)
    log.info("broadcast.file.deleted", file_id=file_id, br_id=br_id)
    return br_id
