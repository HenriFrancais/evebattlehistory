"""Entity directory for a battle report: every character with its corporation and
alliance, so pilot names can be shown with their tickers.

Affiliation comes from the killmail a character appears on (the affiliation at the
time of the fight), else from the stored character row. A name that is on no
killmail and has no character row falls back to the tickers the log parser saw
beside it in the report's logs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import _resolve_char_names
from app.config import Settings
from app.db.models import (
    Alliance,
    BrFight,
    Character,
    Corporation,
    FightKill,
    Killmail,
    KillmailAttacker,
    LogEvent,
)


@dataclass
class EntityCharacter:
    character_id: int
    name: str
    corporation_id: int | None
    alliance_id: int | None


@dataclass
class EntityCorporation:
    corporation_id: int
    name: str | None
    ticker: str | None
    alliance_id: int | None


@dataclass
class EntityAlliance:
    alliance_id: int
    name: str | None
    ticker: str | None


@dataclass
class EntityByName:
    name: str
    corp_ticker: str | None
    alliance_ticker: str | None


@dataclass
class BrEntities:
    characters: list[EntityCharacter]
    corporations: list[EntityCorporation]
    alliances: list[EntityAlliance]
    by_name: list[EntityByName]


async def br_entities(session: AsyncSession, br_id: str, settings: Settings) -> BrEntities:
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return BrEntities(characters=[], corporations=[], alliances=[], by_name=[])
    km_ids = list(
        (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id.in_(fight_ids))
            )
        ).scalars()
    )

    # character_id -> (corporation_id, alliance_id); a victim row wins over attacker rows.
    affiliation: dict[int, tuple[int | None, int | None]] = {}
    if km_ids:
        for cid, corp, alli in (
            await session.execute(
                select(
                    Killmail.victim_character_id,
                    Killmail.victim_corporation_id,
                    Killmail.victim_alliance_id,
                ).where(Killmail.killmail_id.in_(km_ids))
            )
        ).all():
            if cid is not None:
                affiliation[cid] = (corp, alli)
        for cid, corp, alli in (
            await session.execute(
                select(
                    KillmailAttacker.character_id,
                    KillmailAttacker.corporation_id,
                    KillmailAttacker.alliance_id,
                ).where(KillmailAttacker.killmail_id.in_(km_ids))
            )
        ).all():
            if cid is not None:
                affiliation.setdefault(cid, (corp, alli))

    # Log rows of the report: owners, and counterparty names with the tickers seen.
    log_rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.other_corp_ticker,
                LogEvent.other_alliance_ticker,
                func.count(LogEvent.event_id),
            )
            .where(LogEvent.fight_id.in_(fight_ids))
            .group_by(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.other_corp_ticker,
                LogEvent.other_alliance_ticker,
            )
        )
    ).all()
    log_owner_ids = {cid for cid, *_ in log_rows if cid is not None}

    # Characters known only from logs (owners, or a counterparty name that exactly
    # matches a stored character): affiliation from the stored character row.
    other_names = {name for _c, name, *_ in log_rows if name}
    stored = (
        await session.execute(
            select(
                Character.character_id,
                Character.name,
                Character.corporation_id,
                Character.alliance_id,
            ).where(
                Character.character_id.in_(log_owner_ids - set(affiliation))
                | Character.name.in_(other_names)
            )
        )
    ).all()
    for cid, _name, corp, alli in stored:
        affiliation.setdefault(cid, (corp, alli))
    for cid in log_owner_ids:
        affiliation.setdefault(cid, (None, None))

    names = await _resolve_char_names(session, settings, set(affiliation))
    characters = sorted(
        (
            EntityCharacter(
                character_id=cid, name=names[cid], corporation_id=corp, alliance_id=alli
            )
            for cid, (corp, alli) in affiliation.items()
            if cid in names
        ),
        key=lambda c: c.name.lower(),
    )

    corp_ids = {c.corporation_id for c in characters if c.corporation_id is not None}
    corporations = [
        EntityCorporation(
            corporation_id=c.corporation_id, name=c.name, ticker=c.ticker,
            alliance_id=c.alliance_id,
        )
        for c in (
            await session.execute(
                select(Corporation).where(Corporation.corporation_id.in_(corp_ids))
            )
        ).scalars()
    ]
    alli_ids = {c.alliance_id for c in characters if c.alliance_id is not None}
    alliances = [
        EntityAlliance(alliance_id=a.alliance_id, name=a.name, ticker=a.ticker)
        for a in (
            await session.execute(select(Alliance).where(Alliance.alliance_id.in_(alli_ids)))
        ).scalars()
    ]

    # Fallback for names with no character: the ticker pair seen most often.
    known = {c.name.lower() for c in characters}
    seen: dict[str, Counter[tuple[str | None, str | None]]] = {}
    for _cid, name, corp_t, alli_t, n in log_rows:
        if not name or name.lower() in known or not (corp_t or alli_t):
            continue
        seen.setdefault(name, Counter())[(corp_t, alli_t)] += int(n)
    by_name = [
        EntityByName(name=name, corp_ticker=pair[0], alliance_ticker=pair[1])
        for name, pairs in sorted(seen.items(), key=lambda kv: kv[0].lower())
        for pair in [pairs.most_common(1)[0][0]]
    ]
    return BrEntities(
        characters=characters, corporations=corporations, alliances=alliances, by_name=by_name
    )
