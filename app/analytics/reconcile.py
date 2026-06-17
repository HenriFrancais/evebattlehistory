"""Damage reconciliation analytics for NV Battle Reports.

Compares what combat logs say a character dealt/received vs what killmails credit.
The key insight: ``log_damage_out`` typically EXCEEDS ``km_damage_attributed``
because logs capture damage applied to ships that didn't die, while killmails
only attribute damage to ships that were actually destroyed.

This module reads existing LogEvent / LogEventBucket / KillmailAttacker data.
It does NOT re-parse logs or modify Phase 2/3 data.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Character, FightKill, KillmailAttacker, LogEvent, LogEventBucket

#: EWAR / non-damage effect types — excluded from log damage tallies.
_DAMAGE_EFFECT = "damage"


def _as_utc(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts


def _epoch(ts: dt.datetime) -> int:
    return int(_as_utc(ts).timestamp())


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class CharacterReconcileRow:
    """Per-character damage reconciliation for a single fight.

    ``delta = log_damage_out - km_damage_attributed``.
    A positive delta means the pilot applied more damage than killmails credit —
    the gap is damage dealt to ships that didn't die.
    """

    character_id: int
    character_name: str | None
    log_damage_out: float
    """Total outgoing damage from combat logs (direction='out', effect_type='damage')."""
    log_damage_in: float
    """Total incoming damage from combat logs (direction='in', effect_type='damage')."""
    km_damage_attributed: float
    """Total damage_done attributed by killmails for this fight."""
    delta: float
    """log_damage_out - km_damage_attributed (the 'application truth' gap)."""


@dataclass
class DpsPoint:
    """One time-bucket in the outgoing DPS series for a fight."""

    bucket_ts_epoch: int
    """Epoch-second timestamp of the 5-second bucket."""
    sum_damage_out: float
    """Total outgoing damage across all characters in this bucket."""


@dataclass
class FightReconcile:
    """Damage reconciliation result for a single fight."""

    rows: list[CharacterReconcileRow] = field(default_factory=list)
    dps_series: list[DpsPoint] = field(default_factory=list)
    """Outgoing DPS over time: per-bucket sum across all characters in the fight."""


# ---------------------------------------------------------------------------
# Analytics function
# ---------------------------------------------------------------------------


async def fight_damage_reconcile(session: AsyncSession, fight_id: int) -> FightReconcile:
    """Compute damage reconciliation for *fight_id*.

    - Reads LogEvent rows (effect_type='damage') for the fight.
    - Reads KillmailAttacker.damage_done for killmails linked to the fight via FightKill.
    - Reads LogEventBucket rows (effect_type='damage', direction='out') for DPS series.

    Returns empty FightReconcile (no error) when there are no events.
    """
    # --- 1. Log damage totals per character ---
    # Aggregate outgoing and incoming damage directly from LogEvent rows.
    # effect_type='' means unknown; we only want explicit 'damage' rows.
    log_damage_stmt = (
        select(
            LogEvent.character_id,
            LogEvent.direction,
            func.sum(LogEvent.amount).label("total"),
        )
        .where(
            LogEvent.fight_id == fight_id,
            LogEvent.effect_type == _DAMAGE_EFFECT,
            LogEvent.direction.in_(["out", "in"]),
            LogEvent.character_id.is_not(None),
            LogEvent.amount.is_not(None),
        )
        .group_by(LogEvent.character_id, LogEvent.direction)
    )
    log_rows = list((await session.execute(log_damage_stmt)).all())

    # Build {character_id: {direction: total}}
    log_totals: dict[int, dict[str, float]] = {}
    for char_id, direction, total in log_rows:
        if char_id is None:
            continue
        cid = int(char_id)
        if cid not in log_totals:
            log_totals[cid] = {}
        log_totals[cid][direction] = float(total or 0.0)

    # --- 2. KM damage attributed per character ---
    # Join FightKill → KillmailAttacker to sum damage_done per character.
    km_stmt = (
        select(
            KillmailAttacker.character_id,
            func.sum(KillmailAttacker.damage_done).label("total"),
        )
        .join(FightKill, FightKill.killmail_id == KillmailAttacker.killmail_id)
        .where(
            FightKill.fight_id == fight_id,
            KillmailAttacker.character_id.is_not(None),
        )
        .group_by(KillmailAttacker.character_id)
    )
    km_rows = list((await session.execute(km_stmt)).all())
    km_totals: dict[int, float] = {
        int(char_id): float(total or 0.0) for char_id, total in km_rows if char_id is not None
    }

    # --- 3. Merge: union of characters from logs + km ---
    all_char_ids: set[int] = set(log_totals.keys()) | set(km_totals.keys())

    # --- 3b. Look up character names ---
    char_name_rows = list(
        (
            await session.execute(
                select(Character.character_id, Character.name).where(
                    Character.character_id.in_(list(all_char_ids))
                )
            )
        ).all()
    )
    char_names: dict[int, str | None] = {
        int(cid): name for cid, name in char_name_rows
    }

    result_rows: list[CharacterReconcileRow] = []
    for cid in sorted(all_char_ids):
        log_out = log_totals.get(cid, {}).get("out", 0.0)
        log_in = log_totals.get(cid, {}).get("in", 0.0)
        km_attr = km_totals.get(cid, 0.0)
        result_rows.append(
            CharacterReconcileRow(
                character_id=cid,
                character_name=char_names.get(cid),
                log_damage_out=log_out,
                log_damage_in=log_in,
                km_damage_attributed=km_attr,
                delta=log_out - km_attr,
            )
        )

    # --- 4. DPS series from LogEventBucket ---
    # Sum outgoing damage buckets across all characters per timestamp.
    bucket_stmt = (
        select(
            LogEventBucket.bucket_ts,
            func.sum(LogEventBucket.sum_amount).label("total"),
        )
        .where(
            LogEventBucket.fight_id == fight_id,
            LogEventBucket.effect_type == _DAMAGE_EFFECT,
            LogEventBucket.direction == "out",
        )
        .group_by(LogEventBucket.bucket_ts)
        .order_by(LogEventBucket.bucket_ts)
    )
    bucket_rows = list((await session.execute(bucket_stmt)).all())
    dps_series: list[DpsPoint] = [
        DpsPoint(
            bucket_ts_epoch=_epoch(bucket_ts),
            sum_damage_out=float(total or 0.0),
        )
        for bucket_ts, total in bucket_rows
    ]

    return FightReconcile(rows=result_rows, dps_series=dps_series)
