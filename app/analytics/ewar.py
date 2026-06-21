"""EWAR + logi effectiveness analytics for NV Battle Reports.

Surfaces what killmails cannot show: who applied electronic warfare,
who was tackled/jammed/neuted, and which logistics pilots repped whom.

This module reads existing LogEvent data for a fight.
It does NOT re-parse logs or modify Phase 2/3 data.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LogEvent

# ---------------------------------------------------------------------------
# Categorisation constants
# ---------------------------------------------------------------------------

#: effect_types that belong in the tackle/EWAR summary.
# EVE gamelogs do NOT produce combat-effect lines for webifiers, target painters,
# or tracking disruptors — those modules only appear as (notify) deactivation /
# fitting messages, not as per-hit combat entries — so "web", "td", and "paint"
# are intentionally excluded here.  The parser may carry the tokens in its enum
# for completeness, but it will never emit them as LogEvent.effect_type values.
_EWAR_TYPES: frozenset[str] = frozenset({"scram", "disrupt", "jam"})

#: effect_types that belong in the cap-warfare summary.
_CAP_TYPES: frozenset[str] = frozenset({"neut", "nos", "cap_transfer"})

#: effect_types that belong in the logi/reps summary.
# EVE gamelogs do NOT emit "hull repaired" as a combat line (no remote hull rep
# effect type appears in 14k+ real gamelogs), so "rep_hull" is excluded.
_LOGI_TYPES: frozenset[str] = frozenset({"rep_armor", "rep_shield"})


def _as_utc(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class EwarRow:
    """One summary row for tackle/EWAR effects.

    For scram/disrupt: keyed by (source_name, target_name, effect_type, direction)
    from the deduped set (dedupe_suppressed=False only).

    For jam: keyed by (character_id, effect_type, direction) — legacy single-party path.
    """

    character_id: int
    effect_type: str
    direction: str
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime
    source_name: str | None = None
    target_name: str | None = None


@dataclass
class CapRow:
    """One (character, effect_type, direction) summary for cap-warfare effects.

    ``sum_amount`` is the total GJ neuted/nos'd/transferred.
    """

    character_id: int
    effect_type: str
    direction: str
    sum_amount: float
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


@dataclass
class LogiRow:
    """One (character, effect_type, direction) summary for remote repair effects.

    ``sum_amount`` is the total HP repped.
    """

    character_id: int
    effect_type: str
    direction: str
    sum_amount: float
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


@dataclass
class FightEwar:
    """EWAR + logi effectiveness result for a single fight."""

    ewar: list[EwarRow] = field(default_factory=list)
    """Tackle / electronic warfare events (scram, disrupt, jam)."""
    cap: list[CapRow] = field(default_factory=list)
    """Capacitor warfare events (neut, nos, cap_transfer)."""
    logi: list[LogiRow] = field(default_factory=list)
    """Remote repair events (rep_armor, rep_shield)."""


# ---------------------------------------------------------------------------
# Analytics function
# ---------------------------------------------------------------------------

_ALL_TRACKED = _EWAR_TYPES | _CAP_TYPES | _LOGI_TYPES


async def fight_ewar(session: AsyncSession, fight_id: int) -> FightEwar:
    """Compute EWAR + logi effectiveness for *fight_id*.

    scram/disrupt: grouped by (effect_type, source_name, target_name, direction)
        from non-suppressed rows only (dedupe_suppressed=False).  Degenerate rows
        where source_name == target_name are skipped.

    jam: grouped by (character_id, effect_type, direction) — legacy single-party path.

    cap/logi: grouped by (character_id, effect_type, direction) — unchanged.

    Returns empty FightEwar (no error) when there are no relevant events.
    """
    _TACKLE_TYPES: frozenset[str] = frozenset({"scram", "disrupt"})
    _JAM_TYPES: frozenset[str] = frozenset({"jam"})
    _CAP_LOGI_TYPES: frozenset[str] = _CAP_TYPES | _LOGI_TYPES

    ewar_list: list[EwarRow] = []
    cap_list: list[CapRow] = []
    logi_list: list[LogiRow] = []

    # ------------------------------------------------------------------
    # 1. scram / disrupt — source/target-keyed from deduped set
    # ------------------------------------------------------------------
    tackle_stmt = (
        select(
            LogEvent.effect_type,
            LogEvent.source_name,
            LogEvent.target_name,
            LogEvent.direction,
            func.count(LogEvent.event_id).label("event_count"),
            func.min(LogEvent.ts).label("first_ts"),
            func.max(LogEvent.ts).label("last_ts"),
        )
        .where(
            LogEvent.fight_id == fight_id,
            LogEvent.effect_type.in_(list(_TACKLE_TYPES)),
            LogEvent.direction.in_(["out", "in"]),
            LogEvent.dedupe_suppressed.is_(False),
            LogEvent.source_name.is_not(None),
            LogEvent.target_name.is_not(None),
        )
        .group_by(
            LogEvent.effect_type,
            LogEvent.source_name,
            LogEvent.target_name,
            LogEvent.direction,
        )
        .order_by(
            LogEvent.effect_type,
            LogEvent.source_name,
            LogEvent.target_name,
            LogEvent.direction,
        )
    )
    for effect_type, source_name, target_name, direction, event_count, first_ts, last_ts in (
        await session.execute(tackle_stmt)
    ).all():
        if not effect_type or not direction:
            continue
        # skip degenerate rows where tackler == target (impossible tackle)
        if source_name is not None and source_name == target_name:
            continue
        ewar_list.append(EwarRow(
            character_id=0,  # not applicable for tackle rows
            effect_type=effect_type,
            direction=direction,
            event_count=int(event_count),
            first_ts=_as_utc(first_ts),
            last_ts=_as_utc(last_ts),
            source_name=source_name,
            target_name=target_name,
        ))

    # ------------------------------------------------------------------
    # 2. jam — legacy single-party path grouped by character_id
    # ------------------------------------------------------------------
    jam_stmt = (
        select(
            LogEvent.character_id,
            LogEvent.effect_type,
            LogEvent.direction,
            func.count(LogEvent.event_id).label("event_count"),
            func.min(LogEvent.ts).label("first_ts"),
            func.max(LogEvent.ts).label("last_ts"),
        )
        .where(
            LogEvent.fight_id == fight_id,
            LogEvent.effect_type.in_(list(_JAM_TYPES)),
            LogEvent.direction.in_(["out", "in"]),
            LogEvent.character_id.is_not(None),
        )
        .group_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
        .order_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
    )
    for char_id, effect_type, direction, event_count, first_ts, last_ts in (
        await session.execute(jam_stmt)
    ).all():
        if char_id is None or not effect_type or not direction:
            continue
        ewar_list.append(EwarRow(
            character_id=int(char_id),
            effect_type=effect_type,
            direction=direction,
            event_count=int(event_count),
            first_ts=_as_utc(first_ts),
            last_ts=_as_utc(last_ts),
        ))

    # ------------------------------------------------------------------
    # 3. cap / logi — grouped by character_id (unchanged)
    # ------------------------------------------------------------------
    cap_logi_stmt = (
        select(
            LogEvent.character_id,
            LogEvent.effect_type,
            LogEvent.direction,
            func.count(LogEvent.event_id).label("event_count"),
            func.sum(LogEvent.amount).label("sum_amount"),
            func.min(LogEvent.ts).label("first_ts"),
            func.max(LogEvent.ts).label("last_ts"),
        )
        .where(
            LogEvent.fight_id == fight_id,
            LogEvent.effect_type.in_(list(_CAP_LOGI_TYPES)),
            LogEvent.direction.in_(["out", "in"]),
            LogEvent.character_id.is_not(None),
        )
        .group_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
        .order_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
    )
    for char_id, effect_type, direction, event_count, sum_amount, first_ts, last_ts in (
        await session.execute(cap_logi_stmt)
    ).all():
        if char_id is None or not effect_type or not direction:
            continue
        cid = int(char_id)
        et: str = effect_type
        dr: str = direction
        ec: int = int(event_count)
        sa: float = float(sum_amount or 0.0)
        ft: dt.datetime = _as_utc(first_ts)
        lt: dt.datetime = _as_utc(last_ts)

        if et in _CAP_TYPES:
            cap_list.append(CapRow(
                character_id=cid,
                effect_type=et,
                direction=dr,
                sum_amount=sa,
                event_count=ec,
                first_ts=ft,
                last_ts=lt,
            ))
        elif et in _LOGI_TYPES:
            logi_list.append(LogiRow(
                character_id=cid,
                effect_type=et,
                direction=dr,
                sum_amount=sa,
                event_count=ec,
                first_ts=ft,
                last_ts=lt,
            ))

    return FightEwar(ewar=ewar_list, cap=cap_list, logi=logi_list)
