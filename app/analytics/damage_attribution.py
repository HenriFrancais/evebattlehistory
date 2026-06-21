"""Per-loss damage attribution analytics for NV Battle Reports.

Given a killmail_id, returns an ordered list of attackers by damage done
(descending), each with share percentage and final-blow flag, plus the
victim's total damage taken.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Character, Killmail, KillmailAttacker


@dataclass
class AttackerDamageRow:
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    final_blow: bool


@dataclass
class LossDamageAttribution:
    killmail_id: int
    damage_taken: int | None
    total_attributed: int
    attackers: list[AttackerDamageRow]  # sorted by damage_done desc


async def loss_damage_attribution(
    session: AsyncSession,
    killmail_id: int,
) -> LossDamageAttribution:
    """Return ranked damage attribution for one killmail.

    Attackers are sorted by damage_done descending.
    share = damage_done / total_attributed; 0.0 if total_attributed is 0.
    """
    # Fetch damage_taken from the killmail
    km_row = (
        await session.execute(
            select(Killmail.damage_taken).where(Killmail.killmail_id == killmail_id)
        )
    ).one_or_none()
    damage_taken: int | None = km_row[0] if km_row is not None else None

    # Fetch all attacker rows
    attacker_rows = list(
        (
            await session.execute(
                select(
                    KillmailAttacker.character_id,
                    KillmailAttacker.damage_done,
                    KillmailAttacker.final_blow,
                ).where(KillmailAttacker.killmail_id == killmail_id)
            )
        ).all()
    )

    # Resolve character names in one query
    char_ids = {r[0] for r in attacker_rows if r[0] is not None}
    char_names: dict[int, str | None] = {}
    if char_ids:
        for char in (
            await session.execute(
                select(Character.character_id, Character.name).where(
                    Character.character_id.in_(char_ids)
                )
            )
        ).all():
            char_names[char[0]] = char[1]

    total_attributed = sum(r[1] for r in attacker_rows)

    attackers: list[AttackerDamageRow] = []
    for char_id, damage_done, final_blow in attacker_rows:
        share = damage_done / total_attributed if total_attributed > 0 else 0.0
        attackers.append(
            AttackerDamageRow(
                character_id=char_id,
                character_name=char_names.get(char_id) if char_id is not None else None,
                damage_done=damage_done,
                share=share,
                final_blow=bool(final_blow),
            )
        )

    attackers.sort(key=lambda r: r.damage_done, reverse=True)

    return LossDamageAttribution(
        killmail_id=killmail_id,
        damage_taken=damage_taken,
        total_attributed=total_attributed,
        attackers=attackers,
    )
