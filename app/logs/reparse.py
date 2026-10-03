"""Re-parse already-ingested gamelog files with the current parser + SDE splitter
(event cleaning is shared with upload: app.logs.ingest.build_event_rows).

Replaces each file's LogEvent rows in place (delete + re-insert under the same file_id)
and re-stamps them to fights. Uses the stored file on disk — no re-upload needed.

Bulk performance: LogEvent rows are written with a single Core ``insert`` executemany
per file (not ORM ``add_all`` unit-of-work, which dominated the runtime), and the
per-fight tackle dedupe is deferred to ONE pass over all touched fights at the end
rather than re-running for every file that shares a fight.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.associate import (
    _dedupe_ewar_relationships,
    _rebuild_buckets_for_pairs,
    associate_file,
)
from app.logs.ingest import build_event_rows
from app.logs.parse import parse_log
from app.observability.logging import log
from app.sde.load import entity_name_set

_TACKLE_EFFECTS = ("scram", "disrupt")


async def reparse_gamelogs(session: AsyncSession, settings: Settings) -> int:
    """Re-parse every GamelogFile with a readable stored file. Returns count re-parsed."""
    entity_names = await entity_name_set(session)
    files = list((await session.execute(select(GamelogFile))).scalars())
    done = 0
    for gf in files:
        # Per-file guard: a single bad file (read/parse/associate) logs and skips
        # rather than aborting the whole pass.
        try:
            try:
                text = Path(gf.stored_path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("reparse.file_unreadable", file_id=gf.file_id, error=str(exc))
                continue
            parsed = parse_log(text)

            # Capture the fights this file's CURRENT events are stamped to BEFORE we
            # delete them. associate_file's reset only sees the post-delete (None)
            # rows, so without this the buckets for fights the re-parsed events no
            # longer match would be left orphaned.
            old_fight_ids_result = await session.execute(
                select(LogEvent.fight_id)
                .where(LogEvent.file_id == gf.file_id)
                .where(LogEvent.fight_id.is_not(None))
                .distinct()
            )
            old_fight_ids: set[int] = {
                fid for fid in old_fight_ids_result.scalars() if fid is not None
            }

            await session.execute(delete(LogEvent).where(LogEvent.file_id == gf.file_id))
            rows = build_event_rows(
                parsed.events,
                entity_names,
                file_id=gf.file_id,
                character_id=gf.claimed_character_id,
                character_name=gf.character_name,
            )
            # Core bulk insert (single executemany) — far cheaper than ORM add_all +
            # unit-of-work for the hundreds of thousands of rows a full reparse writes.
            if rows:
                await session.execute(insert(LogEvent), rows)
            gf.event_count = len(rows)
            gf.combat_lines = parsed.stats["combat_lines"]
            gf.unmatched_combat = parsed.stats["unmatched_combat"]
            await session.flush()
            # Stamp + bucket-rebuild this file; defer tackle dedupe to the single
            # end-of-run pass below (otherwise it re-runs per file over the same fight).
            try:
                await associate_file(session, gf.file_id, dedupe_tackle=False)
            except Exception as exc:
                log.error("reparse.associate_failed", file_id=gf.file_id, error=str(exc))

            # Rebuild buckets for the old fights so any fight the re-parsed events
            # no longer match has its now-stale LogEventBucket rows cleared. The
            # rebuild reads from ALL remaining LogEvent rows for the pair, so a
            # fight with no surviving events ends up with zero buckets.
            if old_fight_ids is not None and gf.claimed_character_id is not None:
                await _rebuild_buckets_for_pairs(
                    session,
                    {(fid, gf.claimed_character_id) for fid in old_fight_ids},
                )
            done += 1
        except Exception as exc:
            log.warning("reparse.file_failed", file_id=gf.file_id, error=str(exc))
            continue

    # Single tackle-dedupe pass over every fight that has tackle events — replaces the
    # per-file dedupe deferred above so each fight's observations are clustered once.
    tackle_fights: set[int] = {
        fid
        for fid in (
            await session.execute(
                select(LogEvent.fight_id)
                .where(LogEvent.effect_type.in_(_TACKLE_EFFECTS))
                .where(LogEvent.fight_id.is_not(None))
                .distinct()
            )
        ).scalars()
        if fid is not None
    }
    if tackle_fights:
        await _dedupe_ewar_relationships(session, tackle_fights)

    log.info("reparse.done", files=done)
    return done


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker, init_models

    async def _main() -> None:
        settings = get_settings()
        await init_models(settings)  # apply pending schema migrations first
        async with get_sessionmaker(settings)() as session:
            n = await reparse_gamelogs(session, settings)
            await session.commit()
        print(f"re-parsed {n} gamelog files")

    asyncio.run(_main())
