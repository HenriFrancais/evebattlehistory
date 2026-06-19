"""Re-parse already-ingested gamelog files with the fixed parser + SDE splitter.

Replaces each file's LogEvent rows in place (delete + re-insert under the same file_id)
and re-stamps them to fights. Uses the stored file on disk — no re-upload needed.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.associate import associate_file_to_all
from app.logs.entity import split_entity
from app.logs.parse import parse_log
from app.observability.logging import log
from app.sde.load import entity_name_set


async def reparse_gamelogs(session: AsyncSession, settings: Settings) -> int:
    """Re-parse every GamelogFile with a readable stored file. Returns count re-parsed."""
    entity_names = await entity_name_set(session)
    files = list((await session.execute(select(GamelogFile))).scalars())
    done = 0
    for gf in files:
        try:
            text = Path(gf.stored_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("reparse.file_unreadable", file_id=gf.file_id, error=str(exc))
            continue
        parsed = parse_log(text)
        await session.execute(delete(LogEvent).where(LogEvent.file_id == gf.file_id))
        rows: list[LogEvent] = []
        for e in parsed.events:
            other_name, other_ship = e.other_name, e.other_ship_name
            if e.effect_type and e.effect_type != "damage" and not other_ship and other_name:
                char, ship = split_entity(other_name, entity_names)
                other_name = char if char is not None else other_name
                other_ship = ship
            rows.append(LogEvent(
                file_id=gf.file_id, character_id=gf.claimed_character_id, ts=e.ts,
                direction=e.direction, effect_type=e.effect_type, amount=e.amount,
                quality=e.quality, other_name=other_name, other_corp_ticker=e.other_corp_ticker,
                other_alliance_ticker=e.other_alliance_ticker, other_ship_name=other_ship,
                module_name=e.module_name, fight_id=None,
            ))
        if rows:
            session.add_all(rows)
        gf.event_count = len(rows)
        await session.flush()
        await associate_file_to_all(session, gf.file_id)
        done += 1
    log.info("reparse.done", files=done)
    return done


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker

    async def _main() -> None:
        settings = get_settings()
        async with get_sessionmaker(settings)() as session:
            n = await reparse_gamelogs(session, settings)
            await session.commit()
        print(f"re-parsed {n} gamelog files")

    asyncio.run(_main())
