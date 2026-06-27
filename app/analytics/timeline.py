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
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BUCKET_SECONDS, BrFight, Character, Fight, LogEvent, LogEventBucket

#: Remote-assistance effects that friendly counterparties also witness. These are
#: reconstructed from every friendly log that names the character (not just their
#: own log) so a logi with a missing/incomplete log still gets a timeline.
_REMOTE_ASSIST = ("rep_armor", "rep_shield", "cap_transfer")

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


async def _remote_assist_series(
    session: AsyncSession,
    fight_ids: list[int],
    character_id: int,
    char_name: str | None,
) -> tuple[dict[tuple[str, str], dict[int, list[float]]], set[int]]:
    """Reconstruct *character_id*'s remote reps / cap as per-bucket (effect, direction)
    sums, drawing on the character's OWN log AND every friendly log that names them.

    A physical remote-assist tick is logged by both endpoints (the applier as "out",
    the receiver as "in", other_name=applier). We canonicalise each row to
    (applier, receiver, effect, ts, amount), dedupe per tick preferring the applier's
    own record (exactly as composition._reps_applied_by_char), then attribute each
    surviving tick to this character's OUT (they are the applier) or IN (they are the
    receiver). Returns ({(effect, direction): {bucket_epoch: [sum, count]}}, buckets).
    """
    if char_name is None:
        return {}, set()
    rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.direction,
                LogEvent.other_name,
                LogEvent.effect_type,
                LogEvent.amount,
                LogEvent.ts,
            ).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.effect_type.in_(_REMOTE_ASSIST),
                LogEvent.amount.is_not(None),
                or_(LogEvent.character_id == character_id, LogEvent.other_name == char_name),
            )
        )
    ).all()
    if not rows:
        return {}, set()

    owner_ids = {r[0] for r in rows if r[0] is not None}
    names: dict[int, str] = {}
    for cid, nm in (
        await session.execute(
            select(Character.character_id, Character.name).where(
                Character.character_id.in_(owner_ids)
            )
        )
    ).all():
        if nm:
            names[cid] = nm

    # Index every applier-logged ("out") tick so a receiver's duplicate ("in") tick
    # can be matched and dropped. Key mirrors composition: both clients stamp the
    # same server-second for one physical tick.
    out_index: Counter[tuple[str, str, str, dt.datetime, float]] = Counter()
    for owner, direction, other, eff, amount, ts in rows:
        if direction == "out":
            applier = names.get(owner)
            if applier is not None and other is not None:
                out_index[(applier, other, eff, ts, float(amount))] += 1

    kept: list[tuple[str, str, str, dt.datetime, float]] = []
    for owner, direction, other, eff, amount, ts in rows:
        if direction == "out":
            applier = names.get(owner)
            if applier is not None and other is not None:
                kept.append((applier, other, eff, ts, abs(float(amount))))
    consumed: Counter[tuple[str, str, str, dt.datetime, float]] = Counter()
    for owner, direction, other, eff, amount, ts in rows:
        if direction != "in" or other is None:
            continue
        receiver = names.get(owner)
        if receiver is None:
            continue
        key = (other, receiver, eff, ts, float(amount))
        if out_index.get(key, 0) - consumed.get(key, 0) > 0:
            consumed[key] += 1  # duplicate of the applier's authoritative out tick
            continue
        kept.append((other, receiver, eff, ts, abs(float(amount))))

    series: dict[tuple[str, str], dict[int, list[float]]] = {}
    buckets: set[int] = set()
    for applier, receiver, eff, ts, amount in kept:
        if applier == char_name:
            direction = "out"
        elif receiver == char_name:
            direction = "in"
        else:
            continue
        bucket = (_epoch(ts) // BUCKET_SECONDS) * BUCKET_SECONDS
        cell = series.setdefault((eff, direction), {}).setdefault(bucket, [0.0, 0.0])
        cell[0] += amount
        cell[1] += 1
        buckets.add(bucket)
    return series, buckets


async def character_timeline(
    session: AsyncSession,
    br_id: str,
    character_id: int,
) -> CharacterTimeline:
    """Assemble a uPlot-aligned timeline for *character_id* within *br_id*.

    Combines the character's OWN log buckets (damage / EWAR / etc.) with
    remote-assist (reps / cap) reconstructed from every friendly log that names
    them, deduped per tick. Returns empty series (not an error) when neither
    source has any data for this character in the BR.
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

    char_name = (
        await session.execute(
            select(Character.name).where(Character.character_id == character_id)
        )
    ).scalar_one_or_none()

    # Remote-assist (reps / cap) reconstructed from own + friendly logs, deduped.
    ra_series, ra_buckets = await _remote_assist_series(
        session, fight_ids, character_id, char_name
    )

    # --- read own buckets; when reconstructing reps/cap, exclude them here so they
    #     aren't counted twice (the reconstruction already includes this character's
    #     own out/in rep rows). Other effects (damage, EWAR, ...) come from own buckets. ---
    bucket_q = select(LogEventBucket).where(
        LogEventBucket.character_id == character_id,
        LogEventBucket.fight_id.in_(fight_ids),
    )
    if char_name is not None:
        bucket_q = bucket_q.where(LogEventBucket.effect_type.notin_(_REMOTE_ASSIST))
    bucket_rows = list(
        (await session.execute(bucket_q.order_by(LogEventBucket.bucket_ts))).scalars()
    )

    if not bucket_rows and not ra_series:
        return CharacterTimeline(x=[], series=[], fights=fights, t_start=None, t_end=None)

    # --- build sorted unique x axis (own buckets + reconstructed rep/cap buckets) ---
    x_set: set[int] = {_epoch(b.bucket_ts) for b in bucket_rows} | ra_buckets
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

    # --- merge in reconstructed remote-assist series ---
    for (eff, direction), per_bucket in ra_series.items():
        key = f"{_key_part(eff)}:{_key_part(direction)}"
        values_list, total_ec, stored_et, stored_dir = series_data.get(
            key, ([None] * len(x), 0, eff, direction)
        )
        for bucket_epoch, (amt, cnt) in per_bucket.items():
            idx = x_index[bucket_epoch]
            values_list[idx] = (values_list[idx] or 0.0) + amt
            total_ec += int(cnt)
        series_data[key] = (values_list, total_ec, stored_et, stored_dir)

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
