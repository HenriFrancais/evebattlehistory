"""Write-time resolution of off-BR counterparty names → persisted characters.

EVE character names are unique. Counterparty names seen in combat logs that we
do not already have a ``Character`` row for are resolved via ESI (name → id →
corp/alliance affiliation), and the resulting Character/Corporation/Alliance rows
are persisted so the read-path (composition, sides) can use them with no ESI.

This runs ONLY on write paths (log upload). It is best-effort: any ESI failure is
logged and yields no new rows rather than raising, so uploads never fail on ESI.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Alliance, Character, Corporation, LogEvent
from app.observability.logging import log


def _esi_client_for(settings: Settings):  # type: ignore[no-untyped-def]
    """Return the demo or real ESI client per settings.data_source."""
    if settings.data_source == "demo":
        from app.esi.demo import DemoEsiClient

        return DemoEsiClient(settings.demo_data_dir)
    from app.esi.client import get_esi_client

    return get_esi_client(settings)


def _looks_like_name(token: str) -> bool:
    """Cheap pre-filter: a plausible character name has alphanumerics and isn't huge."""
    t = (token or "").strip()
    return bool(t) and any(ch.isalnum() for ch in t) and len(t) <= 40


async def resolve_log_characters(
    session: AsyncSession,
    settings: Settings,
    names: set[str],
    esi=None,  # type: ignore[no-untyped-def]
) -> int:
    """Resolve+persist any *names* not already a known ``Character``. Returns the
    count of newly-persisted characters. Caller commits."""
    candidates = {n.strip() for n in names if _looks_like_name(n)}
    if not candidates:
        return 0

    # Skip names we already have a character for (case-insensitive, EVE names unique).
    known_lower = {
        (nm or "").lower()
        for (nm,) in (
            await session.execute(select(Character.name).where(Character.name.is_not(None)))
        ).all()
    }
    unresolved = sorted(n for n in candidates if n.lower() not in known_lower)
    if not unresolved:
        return 0

    if esi is None:
        esi = _esi_client_for(settings)

    try:
        name_to_id = await esi.resolve_ids(unresolved)  # {name: character_id}
        if not name_to_id:
            return 0
        char_ids = list(dict.fromkeys(name_to_id.values()))
        affil = await esi.resolve_affiliations(char_ids)  # {id: (corp, alli)}
        # Resolve corp/alliance display names.
        entity_ids: list[int] = []
        for corp, alli in affil.values():
            if corp is not None:
                entity_ids.append(corp)
            if alli is not None:
                entity_ids.append(alli)
        ent_names = await esi.resolve_names(list(dict.fromkeys(entity_ids))) if entity_ids else {}
    except Exception as exc:  # ESI down / unexpected shape — best-effort
        log.warning("offbr_resolve.esi_failed", error=str(exc), n=len(unresolved))
        return 0

    now = dt.datetime.now(dt.UTC)
    existing_ids = {
        cid
        for (cid,) in (
            await session.execute(
                select(Character.character_id).where(Character.character_id.in_(char_ids))
            )
        ).all()
    }

    # Persist alliances, then corps (FK → alliance), then characters.
    seen_alli: set[int] = set()
    seen_corp: set[int] = set()
    new_count = 0
    for _corp, alli in affil.values():
        if alli is not None and alli not in seen_alli:
            seen_alli.add(alli)
            await session.merge(
                Alliance(
                    alliance_id=alli,
                    name=ent_names.get(alli, {}).get("name"),
                    last_seen_at=now,
                )
            )
    await session.flush()
    for corp, alli in affil.values():
        if corp is not None and corp not in seen_corp:
            seen_corp.add(corp)
            await session.merge(
                Corporation(
                    corporation_id=corp,
                    name=ent_names.get(corp, {}).get("name"),
                    alliance_id=alli,
                    last_seen_at=now,
                )
            )
    await session.flush()
    for name, cid in name_to_id.items():
        corp, alli = affil.get(cid, (None, None))
        await session.merge(
            Character(
                character_id=cid, name=name, corporation_id=corp,
                alliance_id=alli, last_seen_at=now,
            )
        )
        if cid not in existing_ids:
            new_count += 1
    await session.flush()
    return new_count


async def backfill_log_characters(
    session: AsyncSession, settings: Settings, esi=None  # type: ignore[no-untyped-def]
) -> int:
    """One-time backfill: resolve+persist every counterparty name across all stored
    LogEvents, so off-BR participants in BRs uploaded before this feature existed
    become identifiable. Returns the count of newly-persisted characters.

    Safe to re-run (idempotent: already-known names are skipped). Best-effort on ESI.
    """
    rows = (
        await session.execute(
            select(LogEvent.other_name, LogEvent.source_name, LogEvent.target_name).distinct()
        )
    ).all()
    names = {v for row in rows for v in row if v}
    log.info("offbr_resolve.backfill_start", distinct_names=len(names))
    n = await resolve_log_characters(session, settings, names, esi=esi)
    log.info("offbr_resolve.backfill_done", new_characters=n)
    return n


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker

    async def _main() -> None:
        settings = get_settings()
        async with get_sessionmaker(settings)() as session:
            n = await backfill_log_characters(session, settings)
            await session.commit()
        print(f"resolved + persisted {n} new characters from existing logs")

    asyncio.run(_main())
