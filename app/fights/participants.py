"""Fight participation helper: character IDs that appear in a fight's killmails."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FightKill, Killmail, KillmailAttacker


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
