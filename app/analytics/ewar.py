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
    """One (character, effect_type, direction) summary for tackle/EWAR effects."""

    character_id: int
    effect_type: str
    direction: str
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


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

    Reads LogEvent rows for the fight grouped by (character_id, effect_type, direction),
    then routes each group to the appropriate category (ewar / cap / logi).

    Returns empty FightEwar (no error) when there are no relevant events.
    """
    # Single query: aggregate all tracked effect types, excluding "" (unknown).
    stmt = (
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
            LogEvent.effect_type.in_(list(_ALL_TRACKED)),
            LogEvent.direction.in_(["out", "in"]),
            LogEvent.character_id.is_not(None),
        )
        .group_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
        .order_by(LogEvent.character_id, LogEvent.effect_type, LogEvent.direction)
    )
    agg_rows = list((await session.execute(stmt)).all())

    ewar_list: list[EwarRow] = []
    cap_list: list[CapRow] = []
    logi_list: list[LogiRow] = []

    for char_id, effect_type, direction, event_count, sum_amount, first_ts, last_ts in agg_rows:
        if char_id is None or not effect_type or not direction:
            continue

        cid = int(char_id)
        et: str = effect_type
        dr: str = direction
        ec: int = int(event_count)
        sa: float = float(sum_amount or 0.0)
        ft: dt.datetime = _as_utc(first_ts)
        lt: dt.datetime = _as_utc(last_ts)

        if et in _EWAR_TYPES:
            ewar_list.append(EwarRow(
                character_id=cid,
                effect_type=et,
                direction=dr,
                event_count=ec,
                first_ts=ft,
                last_ts=lt,
            ))
        elif et in _CAP_TYPES:
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
