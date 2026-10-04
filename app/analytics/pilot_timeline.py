"""Per-pilot bucket series for a battle report.

The fleet timeline sums every character's buckets into one series per effect. This
is the same data kept apart per pilot, so the browser can compute a pilot's totals,
peak, average and single-hit range for any time range, and draw the sum of a chosen
set of pilots, without a request per interaction.

Series are sparse: ``idx`` lists the positions in the shared ``x`` axis that have a
value and the other arrays run parallel to it.

These summary series are shown to every viewer. The per-target breakdown behind a
pilot's row is the restricted part (FC/HC, or the pilot's own user) and is served
by the character snapshot endpoint, not from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import (
    _COUNT_EFFECTS,
    _DIRECTION_ORDER,
    _EFFECT_ORDER,
    _KNOWN_EFFECTS,
    _TACKLE_EFFECTS,
    _build_char_side_map,
    _resolve_char_names,
    _resolve_char_ships,
)
from app.analytics.sides_config import load_side_resolver
from app.config import Settings
from app.db.models import (
    BUCKET_SECONDS,
    BrCharShip,
    BrFight,
    InventoryType,
    LogEvent,
    LogEventBucket,
)
from app.fights.offbr_cache import get_offbr_cache
from app.logs.parse import MISS_EFFECT
from app.timeutil import epoch as _epoch

_SERIES_ORDER = (*_EFFECT_ORDER, MISS_EFFECT)


@dataclass
class PilotSeries:
    effect_type: str
    direction: str
    #: Positions in ``PilotTimeline.x``, ascending.
    idx: list[int] = field(default_factory=list)
    #: Absolute amount per bucket; for count effects (EWAR, misses) the count.
    sum: list[float] = field(default_factory=list)
    count: list[int] = field(default_factory=list)
    #: Smallest / largest single hit or cycle in the bucket (None if not recorded).
    min: list[float | None] = field(default_factory=list)
    max: list[float | None] = field(default_factory=list)


@dataclass
class PilotTimelineRow:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str | None
    side_kind: str
    series: list[PilotSeries]


@dataclass
class PilotTimeline:
    x: list[int]
    bucket_seconds: int
    pilots: list[PilotTimelineRow]


def _order(key: tuple[str, str]) -> tuple[int, int]:
    effect, direction = key
    return (
        _SERIES_ORDER.index(effect) if effect in _SERIES_ORDER else len(_SERIES_ORDER),
        _DIRECTION_ORDER.index(direction),
    )


async def pilot_timeline(
    session: AsyncSession,
    br_id: str,
    our_alliance_ids: tuple[int, ...] | list[int],
    our_corp_ids: tuple[int, ...] | list[int],
    settings: Settings,
) -> PilotTimeline:
    """Every logged pilot's bucket series for *br_id*, on the fleet timeline's x axis."""
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return PilotTimeline(x=[], bucket_seconds=BUCKET_SECONDS, pilots=[])

    # The x axis is exactly the fleet timeline's: every bucket holding a known effect.
    x = sorted(
        {
            _epoch(ts)
            for ts in (
                await session.execute(
                    select(LogEventBucket.bucket_ts)
                    .where(
                        LogEventBucket.fight_id.in_(fight_ids),
                        LogEventBucket.effect_type.in_(_KNOWN_EFFECTS),
                        LogEventBucket.direction.in_(_DIRECTION_ORDER),
                    )
                    .distinct()
                )
            ).scalars()
        }
    )
    x_index = {ts: i for i, ts in enumerate(x)}

    # (character, effect, direction) -> {x index: [sum, count, min, max]}
    Cell = list[float | int | None]
    cells: dict[tuple[int, str, str], dict[int, Cell]] = {}

    bucket_rows = (
        await session.execute(
            select(
                LogEventBucket.character_id,
                LogEventBucket.bucket_ts,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
                func.sum(func.abs(LogEventBucket.sum_amount)),
                func.sum(LogEventBucket.event_count),
                func.min(LogEventBucket.min_amount),
                func.max(LogEventBucket.max_amount),
            )
            .where(
                LogEventBucket.fight_id.in_(fight_ids),
                # Tackle is counted from the deduped events below: a bucket holds one
                # copy per on-grid observer, not one per physical tackle.
                LogEventBucket.effect_type.in_((_KNOWN_EFFECTS - _TACKLE_EFFECTS) | {MISS_EFFECT}),
                LogEventBucket.direction.in_(_DIRECTION_ORDER),
            )
            .group_by(
                LogEventBucket.character_id,
                LogEventBucket.bucket_ts,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
            )
        )
    ).all()
    for cid, bucket_ts, effect, direction, amt, cnt, lo, hi in bucket_rows:
        i = x_index.get(_epoch(bucket_ts))
        if i is None:  # a bucket holding nothing but misses is not on the axis
            continue
        counted = effect in _COUNT_EFFECTS or effect == MISS_EFFECT
        value = float(cnt or 0) if counted else float(amt or 0.0)
        cells.setdefault((cid, effect, direction), {})[i] = [
            value,
            int(cnt or 0),
            None if counted else lo,
            None if counted else hi,
        ]

    # Tackle a pilot applied or received, from that pilot's OWN log lines only.
    tackle_rows = (
        await session.execute(
            select(
                LogEvent.character_id, LogEvent.ts, LogEvent.effect_type, LogEvent.direction
            ).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.effect_type.in_(list(_TACKLE_EFFECTS)),
                LogEvent.direction.in_(_DIRECTION_ORDER),
                LogEvent.authoritative.is_(True),
                LogEvent.character_id.is_not(None),
            )
        )
    ).all()
    for cid, ts, effect, direction in tackle_rows:
        i = x_index.get((_epoch(ts) // BUCKET_SECONDS) * BUCKET_SECONDS)
        if i is None:
            continue
        cell = cells.setdefault((cid, effect, direction), {}).setdefault(i, [0.0, 0, None, None])
        cell[0] = float(cell[0] or 0) + 1.0
        cell[1] = int(cell[1] or 0) + 1

    char_ids = {cid for cid, _e, _d in cells}
    names = await _resolve_char_names(session, settings, char_ids)
    ships = await _resolve_char_ships(session, fight_ids)
    # A pilot with no hull on a killmail (logi, links, anyone off the killboard) gets
    # the hull an FC assigned, else the one detected from logs — the same order the
    # composition uses, so the stats table and the Involved tab agree.
    fallback: dict[int, int] = {}
    for oc in await get_offbr_cache().get(session, settings, br_id):
        if oc.detected_ship_type_id is not None:
            fallback[oc.character_id] = oc.detected_ship_type_id
    for cid, sid in (
        await session.execute(
            select(BrCharShip.character_id, BrCharShip.ship_type_id).where(
                BrCharShip.br_id == br_id
            )
        )
    ).all():
        fallback[int(cid)] = int(sid)
    wanted = {cid: sid for cid, sid in fallback.items() if cid in char_ids and cid not in ships}
    if wanted:
        for inv in (
            await session.execute(
                select(InventoryType).where(InventoryType.type_id.in_(set(wanted.values())))
            )
        ).scalars():
            for cid, sid in wanted.items():
                if sid == inv.type_id:
                    ships[cid] = (inv.type_id, inv.name)
    resolver = await load_side_resolver(
        session,
        br_id,
        baseline_alliances=set(our_alliance_ids),
        baseline_corps=set(our_corp_ids),
    )
    sides = await _build_char_side_map(session, fight_ids, resolver)

    by_char: dict[int, list[PilotSeries]] = {}
    for (cid, effect, direction) in sorted(cells, key=lambda k: (k[0], _order((k[1], k[2])))):
        per_bucket = cells[(cid, effect, direction)]
        s = PilotSeries(effect_type=effect, direction=direction)
        for i in sorted(per_bucket):
            total, count, lo, hi = per_bucket[i]
            s.idx.append(i)
            s.sum.append(float(total or 0.0))
            s.count.append(int(count or 0))
            s.min.append(None if lo is None else float(lo))
            s.max.append(None if hi is None else float(hi))
        by_char.setdefault(cid, []).append(s)

    pilots = [
        PilotTimelineRow(
            character_id=cid,
            character_name=names.get(cid, f"Char {cid}"),
            ship_type_id=ships[cid][0] if cid in ships else None,
            ship_name=ships[cid][1] if cid in ships else None,
            side_kind=sides.get(cid, "hostile"),
            series=series,
        )
        for cid, series in by_char.items()
    ]
    pilots.sort(key=lambda p: p.character_name.lower())
    return PilotTimeline(x=x, bucket_seconds=BUCKET_SECONDS, pilots=pilots)
