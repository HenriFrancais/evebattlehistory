"""Fleet-level timeline analytics for NV Battle Reports.

``fleet_timeline`` aggregates LogEventBucket rows across ALL characters for all
fights in a BR, producing four fixed series:
  - dps_out     : effect_type='damage', direction='out', sum_amount-based
  - remote_rep  : effect_type in ('rep_armor','rep_shield'), direction='out', sum_amount-based
  - ewar        : effect_type in ('scram','disrupt','jam'), event_count-based
  - cap_warfare : effect_type in ('neut','nos','cap_transfer'), sum_amount-based

No per-character or per-side filtering is applied; all characters with bucket
rows contribute to the fleet total.

Kill events are derived from FightKill + Killmail + FightSide + InventoryType.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BUCKET_SECONDS,
    BrFight,
    Fight,
    FightKill,
    FightSide,
    InventoryType,
    Killmail,
    LogEventBucket,
)

# ---------------------------------------------------------------------------
# Effect-type membership sets
# ---------------------------------------------------------------------------

_DPS_OUT_EFFECT = "damage"
_DPS_OUT_DIR = "out"

_REMOTE_REP_EFFECTS = frozenset({"rep_armor", "rep_shield"})
_REMOTE_REP_DIR = "out"

_EWAR_EFFECTS = frozenset({"scram", "disrupt", "jam"})
_CAP_WARFARE_EFFECTS = frozenset({"neut", "nos", "cap_transfer"})

# Ordered series keys (always exactly 4)
_SERIES_KEYS = ("dps_out", "remote_rep", "ewar", "cap_warfare")


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


def _as_utc(ts: dt.datetime) -> dt.datetime:
    """Ensure *ts* is UTC-aware; SQLite reads datetimes back without tzinfo."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts


def _epoch(ts: dt.datetime) -> int:
    """Return epoch-seconds for *ts*, normalising naive datetimes to UTC first."""
    return int(_as_utc(ts).timestamp())


@dataclass
class FleetFightInfo:
    """Fight metadata for fight-boundary markers on the fleet timeline."""

    fight_id: int
    seq: int
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    system_id: int


@dataclass
class FleetSeriesOut:
    """One named series aligned to the shared x axis."""

    key: str
    """Fixed key: one of 'dps_out', 'remote_rep', 'ewar', 'cap_warfare'."""
    values: list[float | None]
    """Per-bucket aggregated value, aligned to FleetTimeline.x.
    None where no contributing buckets exist at that timestamp.
    """


@dataclass
class KillEvent:
    """One kill event for the fleet timeline overlay."""

    ts: int
    """Epoch seconds of the killmail."""
    killmail_id: int
    victim_character_id: int | None
    victim_ship_name: str
    side_kind: str | None
    """Side of the victim ('friendly', 'hostile', 'neutral', or None)."""
    isk: float | None
    """Total ISK value of the kill."""


@dataclass
class FleetTimeline:
    """Aggregated fleet-level timeline for one battle report."""

    x: list[int]
    """Sorted, unique epoch-second timestamps of every contributing bucket."""
    series: list[FleetSeriesOut]
    """Always exactly 4 entries in order: dps_out, remote_rep, ewar, cap_warfare."""
    kills: list[KillEvent]
    """Kill events sorted by ts ascending."""
    fights: list[FleetFightInfo]
    """Fight metadata for the BR's fights, ordered by seq."""
    bucket_seconds: int
    """Bucket duration constant (BUCKET_SECONDS from models)."""
    t_start: int | None
    """Earliest bucket timestamp (epoch seconds), or None if no buckets."""
    t_end: int | None
    """Latest bucket timestamp (epoch seconds), or None if no buckets."""


# ---------------------------------------------------------------------------
# Bucket → series routing helpers
# ---------------------------------------------------------------------------


def _classify_bucket(
    effect_type: str,
    direction: str,
    sum_amount: float,
    event_count: int,
) -> tuple[str, float] | None:
    """Return (series_key, contribution) for a bucket, or None if irrelevant."""
    if effect_type == _DPS_OUT_EFFECT and direction == _DPS_OUT_DIR:
        return ("dps_out", sum_amount)
    if effect_type in _REMOTE_REP_EFFECTS and direction == _REMOTE_REP_DIR:
        return ("remote_rep", sum_amount)
    if effect_type in _EWAR_EFFECTS:
        return ("ewar", float(event_count))
    if effect_type in _CAP_WARFARE_EFFECTS:
        return ("cap_warfare", sum_amount)
    return None


# ---------------------------------------------------------------------------
# Main analytics function
# ---------------------------------------------------------------------------


async def fleet_timeline(session: AsyncSession, br_id: str) -> FleetTimeline:
    """Assemble a fleet-level timeline for *br_id*.

    All characters with LogEventBucket rows for the BR's fights contribute.
    Returns empty arrays (not an error) when no buckets or kills exist.
    """
    # 1. Resolve BR fights ordered by seq -----------------------------------------
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

    fights: list[FleetFightInfo] = [
        FleetFightInfo(
            fight_id=fight.fight_id,
            seq=bf.seq,
            started_at=fight.started_at,
            ended_at=fight.ended_at,
            system_id=fight.system_id,
        )
        for bf, fight in bf_rows
    ]
    fight_ids = [f.fight_id for f in fights]

    # Empty series always has 4 keys with empty value lists
    def _empty_series() -> list[FleetSeriesOut]:
        return [FleetSeriesOut(key=k, values=[]) for k in _SERIES_KEYS]

    if not fight_ids:
        return FleetTimeline(
            x=[],
            series=_empty_series(),
            kills=[],
            fights=fights,
            bucket_seconds=BUCKET_SECONDS,
            t_start=None,
            t_end=None,
        )

    # 2. Fetch all LogEventBucket rows for all characters across all BR fights -----
    bucket_rows = list(
        (
            await session.execute(
                select(LogEventBucket).where(
                    LogEventBucket.fight_id.in_(fight_ids),
                )
            )
        ).scalars()
    )

    # 3. Build x-axis from contributing buckets -----------------------------------
    x_set: set[int] = set()
    for b in bucket_rows:
        classified = _classify_bucket(b.effect_type, b.direction, b.sum_amount, b.event_count)
        if classified is not None:
            x_set.add(_epoch(b.bucket_ts))

    x: list[int] = sorted(x_set)
    x_index: dict[int, int] = {ts: i for i, ts in enumerate(x)}

    # 4. Accumulate contributions into per-series value arrays --------------------
    # Initialise with None so missing buckets stay None
    series_values: dict[str, list[float | None]] = {
        key: [None] * len(x) for key in _SERIES_KEYS
    }

    for b in bucket_rows:
        classified = _classify_bucket(b.effect_type, b.direction, b.sum_amount, b.event_count)
        if classified is None:
            continue
        series_key, contribution = classified
        epoch = _epoch(b.bucket_ts)
        if epoch not in x_index:
            continue  # shouldn't happen, but guard
        idx = x_index[epoch]
        current = series_values[series_key][idx]
        series_values[series_key][idx] = (current or 0.0) + contribution

    series: list[FleetSeriesOut] = [
        FleetSeriesOut(key=key, values=series_values[key]) for key in _SERIES_KEYS
    ]

    # 5. Build kills from FightKill + Killmail + FightSide + InventoryType --------
    # Fetch all FightKill rows for the BR fights
    fk_rows = list(
        (
            await session.execute(
                select(FightKill).where(FightKill.fight_id.in_(fight_ids))
            )
        ).scalars()
    )

    km_ids = [fk.killmail_id for fk in fk_rows]

    kills: list[KillEvent] = []
    if km_ids:
        # Fetch killmails
        km_rows: list[Killmail] = list(
            (
                await session.execute(
                    select(Killmail).where(Killmail.killmail_id.in_(km_ids))
                )
            ).scalars()
        )
        km_map: dict[int, Killmail] = {km.killmail_id: km for km in km_rows}

        # Fetch InventoryType names for all victim ship type ids
        ship_type_ids = {km.victim_ship_type_id for km in km_map.values()
                         if km.victim_ship_type_id is not None}
        ship_name_map: dict[int, str] = {}
        if ship_type_ids:
            for inv in (
                await session.execute(
                    select(InventoryType).where(InventoryType.type_id.in_(ship_type_ids))
                )
            ).scalars():
                ship_name_map[inv.type_id] = inv.name

        # Fetch FightSide rows keyed by (fight_id, side_idx)
        fs_rows = list(
            (
                await session.execute(
                    select(FightSide).where(FightSide.fight_id.in_(fight_ids))
                )
            ).scalars()
        )
        side_kind_map: dict[tuple[int, int], str | None] = {
            (fs.fight_id, fs.side_idx): fs.side_kind for fs in fs_rows
        }

        # Build FightKill lookup: killmail_id → (fight_id, side_idx)
        fk_lookup: dict[int, tuple[int, int]] = {
            fk.killmail_id: (fk.fight_id, fk.side_idx) for fk in fk_rows
        }

        for km_id in km_ids:
            km = km_map.get(km_id)
            if km is None:
                continue
            fight_id_for_km, side_idx = fk_lookup[km_id]
            side_kind = side_kind_map.get((fight_id_for_km, side_idx))
            ship_name = (
                ship_name_map.get(km.victim_ship_type_id, "Unknown")
                if km.victim_ship_type_id is not None
                else "Unknown"
            )
            kills.append(
                KillEvent(
                    ts=_epoch(km.killmail_time),
                    killmail_id=km_id,
                    victim_character_id=km.victim_character_id,
                    victim_ship_name=ship_name,
                    side_kind=side_kind,
                    isk=km.total_value,
                )
            )

        kills.sort(key=lambda k: k.ts)

    return FleetTimeline(
        x=x,
        series=series,
        kills=kills,
        fights=fights,
        bucket_seconds=BUCKET_SECONDS,
        t_start=x[0] if x else None,
        t_end=x[-1] if x else None,
    )
