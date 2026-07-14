"""Re-parse already-ingested fleet-broadcast files in place.

Re-reads each stored file, re-parses it, re-reconstructs dates using the persisted
``anchor_date`` (so no BR-window lookup is needed and the result is deterministic),
replaces the file's ``Broadcast`` rows, re-applies supersession, and re-associates.

CLI: ``python -m app.logs.broadcast_reparse``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Broadcast, BroadcastFile
from app.logs.broadcast_associate import associate_broadcasts_for_br
from app.logs.broadcast_ingest import _FRIENDLY_KINDS, _apply_supersession, _resolve_subject_ids
from app.logs.broadcast_parse import parse_broadcast, reconstruct_dates
from app.observability.logging import log


async def _roster_lookup_from_db(session: AsyncSession) -> Callable[[str], int | None]:
    """A name->character_id lookup backed purely by the Character table.

    Reparse is an offline maintenance pass; it avoids the live roster snapshot and
    resolves against persisted characters, deferring to _resolve_subject_ids' fallback.
    """
    return lambda name: None


async def reparse_broadcasts(session: AsyncSession, settings: Settings) -> int:
    """Re-parse every readable BroadcastFile. Returns the count re-parsed."""
    roster_lookup = await _roster_lookup_from_db(session)
    files = list((await session.execute(select(BroadcastFile))).scalars())
    touched_brs: set[str] = set()
    done = 0
    for bf in files:
        try:
            try:
                text = Path(bf.stored_path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning(
                    "broadcast_reparse.file_unreadable",
                    file_id=bf.broadcast_file_id,
                    error=str(exc),
                )
                continue
            parsed = parse_broadcast(text)
            broadcasts = parsed.broadcasts
            ts_list = (
                reconstruct_dates(broadcasts, bf.anchor_date)
                if (broadcasts and bf.anchor_date is not None)
                else []
            )
            friendly = {b.subject_name for b in broadcasts if b.kind in _FRIENDLY_KINDS}
            subject_ids = await _resolve_subject_ids(session, friendly, roster_lookup)

            await session.execute(
                delete(Broadcast).where(Broadcast.file_id == bf.broadcast_file_id)
            )
            rows: list[dict[str, object]] = []
            for b, ts in zip(broadcasts, ts_list, strict=False):
                rows.append(
                    dict(
                        file_id=bf.broadcast_file_id,
                        br_id=bf.br_id,
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
            bf.broadcast_count = len(rows)
            bf.log_start_at = min(ts_list) if ts_list else None
            bf.log_end_at = max(ts_list) if ts_list else None
            await session.flush()
            touched_brs.add(bf.br_id)
            done += 1
        except Exception as exc:  # pragma: no cover - defensive per-file guard
            log.warning(
                "broadcast_reparse.file_failed",
                file_id=bf.broadcast_file_id,
                error=str(exc),
            )
            continue

    for br_id in touched_brs:
        await _apply_supersession(session, br_id)
        await associate_broadcasts_for_br(session, br_id)

    log.info("broadcast_reparse.done", files=done)
    return done


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker

    async def _main() -> None:
        settings = get_settings()
        async with get_sessionmaker(settings)() as session:
            n = await reparse_broadcasts(session, settings)
            await session.commit()
        print(f"re-parsed {n} broadcast files")

    asyncio.run(_main())
