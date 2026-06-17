"""Fight participation helpers.

Public API
----------
    char_ids = await fight_participant_char_ids(session, fight_id)
    logged   = await br_logged_char_ids(session, br_id)
    parts    = await br_participants(session, settings, br_id)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import BrFight, Character, FightKill, Killmail, KillmailAttacker, LogEvent
from app.roster.snapshot import get_roster_store


async def fight_participant_char_ids(session: AsyncSession, fight_id: int) -> set[int]:
    """Return distinct character_ids that appear as victim OR attacker in the fight.

    Joins FightKill → Killmail/KillmailAttacker.  Returns a set (may be empty).
    """
    # Victim character_ids
    victim_result = await session.execute(
        select(Killmail.victim_character_id)
        .join(FightKill, FightKill.killmail_id == Killmail.killmail_id)
        .where(FightKill.fight_id == fight_id)
        .where(Killmail.victim_character_id.is_not(None))
    )
    char_ids: set[int] = {cid for cid in victim_result.scalars() if cid is not None}

    # Attacker character_ids
    attacker_result = await session.execute(
        select(KillmailAttacker.character_id)
        .join(FightKill, FightKill.killmail_id == KillmailAttacker.killmail_id)
        .where(FightKill.fight_id == fight_id)
        .where(KillmailAttacker.character_id.is_not(None))
    )
    char_ids.update(cid for cid in attacker_result.scalars() if cid is not None)

    return char_ids


async def br_logged_char_ids(session: AsyncSession, br_id: str) -> set[int]:
    """Return distinct character_ids with at least one LogEvent stamped with a BR fight.

    These are characters that had logs uploaded and associated to a fight in this BR,
    regardless of whether they appeared on any killmail (E1: log-only participants).
    """
    fight_id_result = await session.execute(
        select(BrFight.fight_id).where(BrFight.br_id == br_id)
    )
    fight_ids = list(fight_id_result.scalars())
    if not fight_ids:
        return set()

    result = await session.execute(
        select(LogEvent.character_id)
        .where(
            LogEvent.fight_id.in_(fight_ids),
            LogEvent.character_id.is_not(None),
        )
        .distinct()
    )
    return {cid for cid in result.scalars() if cid is not None}


@dataclass
class ParticipantInfo:
    """Participant in a BR: union of killmail participants and logged characters."""

    character_id: int
    character_name: str | None
    user_name: str | None
    on_killmail: bool
    has_logs: bool
    fight_ids: list[int] = field(default_factory=list)


async def br_participants(
    session: AsyncSession,
    settings: Settings,
    br_id: str,
) -> list[ParticipantInfo]:
    """Return the union of killmail participants and logged characters for a BR.

    Each entry carries ``on_killmail`` (appeared on ≥1 killmail) and ``has_logs``
    (has ≥1 LogEvent stamped with a fight in this BR).  A character may have both
    flags true, only one, or (for killmail participants) neither log flag.

    Roster is consulted for user_name and character_name; falls back to the
    Character table for names not in the roster.
    """
    roster = await get_roster_store(settings).get()

    # 1. Collect fight_ids for this BR
    fight_id_result = await session.execute(
        select(BrFight.fight_id).where(BrFight.br_id == br_id)
    )
    fight_ids = list(fight_id_result.scalars())
    if not fight_ids:
        return []

    # 2. Killmail participants per fight
    km_char_to_fights: dict[int, set[int]] = {}
    for fight_id in fight_ids:
        km_chars = await fight_participant_char_ids(session, fight_id)
        for cid in km_chars:
            km_char_to_fights.setdefault(cid, set()).add(fight_id)

    # 3. Logged characters: character_id → set of fight_ids (from stamped LogEvent rows)
    log_result = await session.execute(
        select(LogEvent.character_id, LogEvent.fight_id)
        .where(
            LogEvent.fight_id.in_(fight_ids),
            LogEvent.character_id.is_not(None),
        )
        .distinct()
    )
    log_char_to_fights: dict[int, set[int]] = {}
    for cid, fid in log_result.fetchall():
        if cid is not None and fid is not None:
            log_char_to_fights.setdefault(int(cid), set()).add(int(fid))

    # 4. Union of all character_ids
    all_char_ids: set[int] = set(km_char_to_fights.keys()) | set(log_char_to_fights.keys())
    if not all_char_ids:
        return []

    # 5. Load character names from Character table (covers all char_ids)
    char_name_result = await session.execute(
        select(Character.character_id, Character.name).where(
            Character.character_id.in_(list(all_char_ids))
        )
    )
    db_char_names: dict[int, str | None] = {
        int(cid): name for cid, name in char_name_result.fetchall()
    }

    # Roster name lookups (override DB if available; roster is more authoritative for users)
    roster_char_names: dict[int, str] = {}
    for user in roster.users:
        for c in user.characters:
            roster_char_names[c.character_id] = c.character_name

    # 6. Build output list
    result_list: list[ParticipantInfo] = []
    for cid in sorted(all_char_ids):
        km_fights = km_char_to_fights.get(cid, set())
        log_fights = log_char_to_fights.get(cid, set())
        all_fights = sorted(km_fights | log_fights)

        char_name = roster_char_names.get(cid) or db_char_names.get(cid)
        user_name = roster.char_to_user.get(cid)

        result_list.append(
            ParticipantInfo(
                character_id=cid,
                character_name=char_name,
                user_name=user_name,
                on_killmail=bool(km_fights),
                has_logs=bool(log_fights),
                fight_ids=all_fights,
            )
        )

    return result_list
