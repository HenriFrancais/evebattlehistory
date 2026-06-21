"""Per-loss damage attribution analytics for NV Battle Reports.

Given a killmail_id, returns an ordered list of attackers by damage done
(descending), each with share percentage and final-blow flag, plus the
victim's total damage taken.

Also provides battle-level leaderboard: summing KillmailAttacker.damage_done
per attacker character_id across ALL kills in a BR (killmail-only DPS proxy).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrFight, Character, FightKill, Killmail, KillmailAttacker


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


# ---------------------------------------------------------------------------
# Task 16: Battle-level damage leaderboard
# ---------------------------------------------------------------------------


@dataclass
class LeaderboardRow:
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    log_damage_out: float | None  # None unless logs present (filled in Task 21)


@dataclass
class BrDamageLeaderboard:
    rows: list[LeaderboardRow]  # sorted by damage_done desc
    total_attributed: int
    logs_present: bool


async def br_damage_leaderboard(
    session: AsyncSession,
    br_id: str,
) -> BrDamageLeaderboard:
    """Return ranked damage leaderboard for a whole battle report.

    Gathers all FightKill.killmail_id for the BR's fights via the canonical
    BrFight JOIN FightKill join (same as composition.py / Task 15 guard),
    then sums KillmailAttacker.damage_done grouped by character_id.

    log_damage_out is None and logs_present is False (Task 21 wires the overlay).
    """
    # Collect all killmail_ids for this BR via BrFight → FightKill join
    km_id_rows = (
        await session.execute(
            select(FightKill.killmail_id)
            .join(BrFight, BrFight.fight_id == FightKill.fight_id)
            .where(BrFight.br_id == br_id)
        )
    ).all()
    km_ids = [r[0] for r in km_id_rows]

    if not km_ids:
        return BrDamageLeaderboard(rows=[], total_attributed=0, logs_present=False)

    # Sum damage_done per character_id across all killmails in this BR
    agg_rows = list(
        (
            await session.execute(
                select(
                    KillmailAttacker.character_id,
                    func.sum(KillmailAttacker.damage_done).label("total_damage"),
                )
                .where(KillmailAttacker.killmail_id.in_(km_ids))
                .group_by(KillmailAttacker.character_id)
            )
        ).all()
    )

    grand_total = sum(int(r[1]) for r in agg_rows)

    # Resolve character names in one query
    char_ids = {r[0] for r in agg_rows if r[0] is not None}
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

    rows: list[LeaderboardRow] = []
    for char_id, total_damage in agg_rows:
        damage_done = int(total_damage)
        share = damage_done / grand_total if grand_total > 0 else 0.0
        rows.append(
            LeaderboardRow(
                character_id=char_id,
                character_name=char_names.get(char_id) if char_id is not None else None,
                damage_done=damage_done,
                share=share,
                log_damage_out=None,
            )
        )

    rows.sort(key=lambda r: r.damage_done, reverse=True)

    return BrDamageLeaderboard(
        rows=rows,
        total_attributed=grand_total,
        logs_present=False,
    )
