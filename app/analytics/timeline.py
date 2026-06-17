"""Per-character timeline analytics for NV Battle Reports.

``character_timeline`` builds a uPlot-aligned structure from LogEventBucket rows.
``character_timeline_events`` returns raw LogEvent rows capped for drill-down.

NULL → "" convention (LogEventBucket)
--------------------------------------
The association pipeline stores ``effect_type="" `` and ``direction=""`` when the
source LogEvent had ``None`` for those columns (SQLite does not allow NULL in
composite primary keys).  This module surfaces those "" values as:
  - ``effect_type=None`` / ``direction=None`` in Pydantic output fields.
  - ``key="unknown:unknown"`` (or ``"unknown:<dir>"`` / ``"<et>:unknown"``) in the
    series key used by uPlot for label display.

Callers must never treat "" as a meaningful named effect type or direction.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrFight, Fight, LogEvent, LogEventBucket

#: Maximum raw events returned by character_timeline_events (drill-down cap).
EVENTS_CAP: int = 1000

_UNKNOWN = "unknown"


def _as_utc(ts: dt.datetime) -> dt.datetime:
    """Ensure *ts* is UTC-aware.

    SQLite stores datetimes as naive strings; SQLAlchemy reads them back without
    tzinfo.  All timestamps in this app are UTC, so we attach UTC when tzinfo is
    missing.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts


def _epoch(ts: dt.datetime) -> int:
    """Return epoch-seconds for *ts*, normalising naive datetimes to UTC first."""
    return int(_as_utc(ts).timestamp())


def _label(raw: str) -> str | None:
    """Convert "" → None; pass through any other value unchanged.

    The returned value is used in Pydantic output fields (effect_type / direction).
    """
    return None if raw == "" else raw


def _key_part(raw: str) -> str:
    """Convert "" → "unknown" for use in the series key string."""
    return _UNKNOWN if raw == "" else raw


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class FightInfo:
    """Fight metadata included in CharacterTimeline for marker rendering."""

    fight_id: int
    seq: int
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    system_id: int


@dataclass
class TimelineSeries:
    """One (effect_type, direction) series aligned to the shared x axis."""

    key: str
    """uPlot series key: ``"{effect_type_label}:{direction_label}"``
    (uses ``"unknown"`` where the source value was ``""``).
    """
    effect_type: str | None
    """``None`` when the source effect_type was ``""`` (unknown)."""
    direction: str | None
    """``None`` when the source direction was ``""`` (unknown)."""
    values: list[float | None]
    """Sum-amount per bucket, aligned to ``CharacterTimeline.x``.
    ``None`` where this series has no bucket at that timestamp.
    """
    event_count: int
    """Total event count across all buckets in this series."""


@dataclass
class CharacterTimeline:
    """uPlot-aligned timeline for one character within one battle report."""

    x: list[int]
    """Sorted, unique epoch-second timestamps of every bucket across all series."""
    series: list[TimelineSeries]
    fights: list[FightInfo]
    t_start: int | None
    """Earliest bucket timestamp (epoch seconds), or None if no buckets."""
    t_end: int | None
    """Latest bucket timestamp (epoch seconds), or None if no buckets."""


@dataclass
class TimelineEvent:
    """One raw LogEvent row for drill-down display."""

    ts: dt.datetime
    direction: str | None
    effect_type: str | None
    amount: float | None
    quality: str | None
    other_name: str | None
    other_ship_name: str | None
    module_name: str | None


@dataclass
class TimelineEventList:
    """Capped list of raw events with a truncation flag."""

    events: list[TimelineEvent]
    truncated: bool
    """True when the result was capped at EVENTS_CAP and more rows exist."""


# ---------------------------------------------------------------------------
# Analytics functions
# ---------------------------------------------------------------------------


async def character_timeline(
    session: AsyncSession,
    br_id: str,
    character_id: int,
) -> CharacterTimeline:
    """Assemble a uPlot-aligned timeline for *character_id* within *br_id*.

    Returns empty series (not an error) when the character has no LogEventBucket
    rows for any fight in this BR.
    """
    # --- resolve BR fights ordered by seq ---
    bf_rows = list(
        (
            await session.execute(
                select(BrFight, Fight)
                .join(Fight, Fight.fight_id == BrFight.fight_id)
                .where(BrFight.br_id == br_id)
                .order_by(BrFight.seq)
            )
        ).all()
    )

    fights: list[FightInfo] = [
        FightInfo(
            fight_id=fight.fight_id,
            seq=bf.seq,
            started_at=fight.started_at,
            ended_at=fight.ended_at,
            system_id=fight.system_id,
        )
        for bf, fight in bf_rows
    ]
    fight_ids = [f.fight_id for f in fights]

    if not fight_ids:
        return CharacterTimeline(x=[], series=[], fights=fights, t_start=None, t_end=None)

    # --- read all buckets for this character across BR fights (single query) ---
    bucket_rows = list(
        (
            await session.execute(
                select(LogEventBucket)
                .where(
                    LogEventBucket.character_id == character_id,
                    LogEventBucket.fight_id.in_(fight_ids),
                )
                .order_by(LogEventBucket.bucket_ts)
            )
        ).scalars()
    )

    if not bucket_rows:
        return CharacterTimeline(x=[], series=[], fights=fights, t_start=None, t_end=None)

    # --- build sorted unique x axis ---
    x_set: set[int] = {_epoch(b.bucket_ts) for b in bucket_rows}
    x: list[int] = sorted(x_set)
    x_index: dict[int, int] = {ts: i for i, ts in enumerate(x)}

    # --- group buckets by (effect_type, direction) ---
    # key → (sum_amount per x-index, total event_count, raw effect_type, raw direction)
    series_data: dict[str, tuple[list[float | None], int, str, str]] = {}
    for b in bucket_rows:
        raw_et: str = b.effect_type
        raw_dir: str = b.direction
        key = f"{_key_part(raw_et)}:{_key_part(raw_dir)}"
        if key not in series_data:
            values: list[float | None] = [None] * len(x)
            series_data[key] = (values, 0, raw_et, raw_dir)

        values_list, total_ec, stored_et, stored_dir = series_data[key]
        idx = x_index[_epoch(b.bucket_ts)]
        current = values_list[idx]
        values_list[idx] = (current or 0.0) + b.sum_amount
        series_data[key] = (values_list, total_ec + b.event_count, stored_et, stored_dir)

    series: list[TimelineSeries] = [
        TimelineSeries(
            key=key,
            effect_type=_label(raw_et),
            direction=_label(raw_dir),
            values=values_list,
            event_count=total_ec,
        )
        for key, (values_list, total_ec, raw_et, raw_dir) in sorted(series_data.items())
    ]

    return CharacterTimeline(
        x=x,
        series=series,
        fights=fights,
        t_start=x[0] if x else None,
        t_end=x[-1] if x else None,
    )


async def character_timeline_events(
    session: AsyncSession,
    br_id: str,
    character_id: int,
    t_from: int,
    t_to: int,
    effect_type: str | None = None,
    direction: str | None = None,
) -> TimelineEventList:
    """Return raw LogEvent rows for *character_id* within [t_from, t_to].

    Only events whose fight_id belongs to this BR are included.
    Results are ordered by ts ascending and capped at EVENTS_CAP rows.
    When capped, ``truncated=True`` is set on the result.

    ``effect_type`` / ``direction`` are matched against the raw DB value
    (which stores ``""`` for unknown, not ``None``).
    """
    # Resolve fight_ids for this BR
    fight_id_rows = list(
        (
            await session.execute(
                select(BrFight.fight_id).where(BrFight.br_id == br_id)
            )
        ).scalars()
    )
    fight_ids = list(fight_id_rows)

    # Build time window
    from_dt = dt.datetime.fromtimestamp(t_from, tz=dt.UTC)
    to_dt = dt.datetime.fromtimestamp(t_to, tz=dt.UTC)

    stmt = (
        select(LogEvent)
        .where(
            LogEvent.character_id == character_id,
            LogEvent.fight_id.in_(fight_ids),
            LogEvent.ts >= from_dt,
            LogEvent.ts <= to_dt,
        )
        .order_by(LogEvent.ts)
    )

    if effect_type is not None:
        stmt = stmt.where(LogEvent.effect_type == effect_type)
    if direction is not None:
        stmt = stmt.where(LogEvent.direction == direction)

    stmt = stmt.limit(EVENTS_CAP + 1)  # fetch one extra to detect truncation

    rows = list((await session.execute(stmt)).scalars())
    truncated = len(rows) > EVENTS_CAP
    if truncated:
        rows = rows[:EVENTS_CAP]

    events = [
        TimelineEvent(
            ts=row.ts,
            direction=row.direction or None,
            effect_type=row.effect_type or None,
            amount=row.amount,
            quality=row.quality,
            other_name=row.other_name,
            other_ship_name=row.other_ship_name,
            module_name=row.module_name,
        )
        for row in rows
    ]
    return TimelineEventList(events=events, truncated=truncated)
