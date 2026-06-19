"""Fleet-level timeline analytics for NV Battle Reports.

``fleet_timeline`` aggregates LogEventBucket rows across ALL characters for all
fights in a BR into one series per ``(effect_type, direction)`` pair that has
data. Each series carries:

  - ``key``         : ``"{effect_type}:{direction}"`` — stable identity for toggles
  - ``effect_type`` : e.g. ``damage``, ``rep_armor``, ``neut``, ``scram``
  - ``direction``   : ``"out"`` or ``"in"``
  - ``metric``      : ``"amount"`` (HP/GJ, from sum_amount) or ``"count"`` (EWAR events)
  - ``values``      : per-bucket MAGNITUDE aligned to ``x`` (None where no data)

Values are magnitudes (``abs(sum_amount)`` for amount effects, ``event_count``
for count effects); the *direction* field tells the presentation layer whether
to draw the series above (out) or below (in) a mirrored baseline. Sign in the
raw logs is inconsistent across cap/neut effects, so magnitude + direction is
the reliable encoding.

No per-character or per-side filtering is applied here; all characters with
bucket rows contribute to the fleet total. Target-side splitting (friendly /
hostile / anomaly) is a later workstream that adds an ``other_side`` dimension.

Kill events are derived from FightKill + Killmail + FightSide + InventoryType.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import classify_entity
from app.analytics.weapons import classify_weapon
from app.config import Settings
from app.observability.logging import log
from app.db.models import (
    BUCKET_SECONDS,
    BrFight,
    Character,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    LogEvent,
    LogEventBucket,
)

# Effect → panel group (matches the frontend grouping).
_EFFECT_GROUP: dict[str, str] = {
    "damage": "damage", "rep_armor": "damage", "rep_shield": "damage",
    "neut": "cap", "nos": "cap", "cap_transfer": "cap",
    "scram": "ewar", "disrupt": "ewar", "jam": "ewar",
}

# Strip in-game corp [TICKER] and alliance <TICKER> tags (incl. HTML-encoded)
# left on some EWAR/cap log targets, e.g. "Proteus Nate Marston [NVACA] &lt;NV&gt;".
_TAG_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")


def _clean_target_name(name: str | None) -> str:
    if not name:
        return "?"
    s = _TAG_RE.sub("", name)
    return re.sub(r"\s{2,}", " ", s).strip() or "?"


@dataclass
class Contribution:
    """One source→target aggregate within a single time bucket."""

    source_character_id: int | None
    source_name: str
    target_name: str
    effect_type: str
    direction: str
    group: str
    value: float
    module_name: str | None = None
    icon_type_id: int | None = None
    weapon_category: str | None = None


async def _resolve_char_names(
    session: AsyncSession, settings: Settings, char_ids: set[int]
) -> dict[int, str]:
    """Resolve character ids → names: DB Character first, then ESI for the rest
    (persisting newly-resolved names back to Character as a cache)."""
    if not char_ids:
        return {}
    names: dict[int, str] = {}
    for ch in (
        await session.execute(select(Character).where(Character.character_id.in_(char_ids)))
    ).scalars():
        if ch.name:
            names[ch.character_id] = ch.name

    missing = [cid for cid in char_ids if cid not in names]
    if missing:
        try:
            if settings.data_source == "demo":
                from app.esi.demo import DemoEsiClient

                esi: object = DemoEsiClient(settings.demo_data_dir)
            else:
                from app.esi.client import get_esi_client

                esi = get_esi_client(settings)
            resolved = await esi.resolve_names(missing)  # type: ignore[attr-defined]
            now = dt.datetime.now(dt.UTC)
            for cid, info in resolved.items():
                nm = info.get("name") if isinstance(info, dict) else None
                if not nm:
                    continue
                names[cid] = nm
                await session.merge(Character(character_id=cid, name=nm, last_seen_at=now))
            await session.commit()
        except Exception as exc:  # network/ESI failure — fall back to ids
            log.warning("contributions.name_resolve_failed", error=str(exc))
    return names


async def fleet_contributions(
    session: AsyncSession, br_id: str, at_epoch: int, settings: Settings
) -> list[Contribution]:
    """Break down ALL activity at a single 5s bucket into source→target rows
    (which of our pilots applied what to whom), grouped by type and sorted
    most→least. Source ids are resolved to names (DB → ESI)."""
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return []

    bucket = (at_epoch // BUCKET_SECONDS) * BUCKET_SECONDS
    start = dt.datetime.fromtimestamp(bucket, tz=dt.UTC).replace(tzinfo=None)
    end = dt.datetime.fromtimestamp(bucket + BUCKET_SECONDS, tz=dt.UTC).replace(tzinfo=None)

    rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.effect_type,
                LogEvent.direction,
                LogEvent.amount,
                LogEvent.module_name,
            ).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.ts >= start,
                LogEvent.ts < end,
                LogEvent.effect_type.in_(_KNOWN_EFFECTS),
            )
        )
    ).all()

    agg: dict[tuple[int | None, str, str, str], float] = {}
    # Per damage group, track HP per module so we can pick the dominant weapon.
    module_dmg: dict[tuple[int | None, str, str, str], dict[str, float]] = {}
    for cid, other, eff, direction, amount, module in rows:
        key = (cid, _clean_target_name(other), eff or "", direction or "")
        contrib = 1.0 if eff in _COUNT_EFFECTS else abs(amount or 0.0)
        agg[key] = agg.get(key, 0.0) + contrib
        if eff == "damage" and module:
            module_dmg.setdefault(key, {})[module] = (
                module_dmg.setdefault(key, {}).get(module, 0.0) + abs(amount or 0.0)
            )

    # Dominant module per damage group + the family fallback names it may need.
    top_module: dict[tuple[int | None, str, str, str], str] = {
        key: max(mods.items(), key=lambda kv: kv[1])[0] for key, mods in module_dmg.items()
    }
    wanted_names: set[str] = set()
    for name in top_module.values():
        wanted_names.add(name)
        fb = classify_weapon(name).fallback_name
        if fb:
            wanted_names.add(fb)
    name_to_type: dict[str, int] = {}
    if wanted_names:
        for inv in (
            await session.execute(
                select(InventoryType).where(InventoryType.name.in_(wanted_names))
            )
        ).scalars():
            name_to_type[inv.name] = inv.type_id

    names = await _resolve_char_names(session, settings, {k[0] for k in agg if k[0] is not None})

    out: list[Contribution] = []
    for (cid, other, eff, direction), val in agg.items():
        module = top_module.get((cid, other, eff, direction))
        icon_type_id: int | None = None
        category: str | None = None
        if module is not None:
            wc = classify_weapon(module)
            category = wc.category
            icon_type_id = name_to_type.get(module) or (
                name_to_type.get(wc.fallback_name) if wc.fallback_name else None
            )
        out.append(
            Contribution(
                source_character_id=cid,
                source_name=(names.get(cid) or f"Char {cid}") if cid is not None else "?",
                target_name=other,
                effect_type=eff,
                direction=direction,
                group=_EFFECT_GROUP.get(eff, "other"),
                value=val,
                module_name=module,
                icon_type_id=icon_type_id,
                weapon_category=category,
            )
        )
    out.sort(key=lambda c: c.value, reverse=True)
    return out

# ---------------------------------------------------------------------------
# Effect taxonomy
# ---------------------------------------------------------------------------

#: Effects whose magnitude is an accumulated amount (HP for damage/reps, GJ for
#: cap warfare). Plotted from ``abs(sum_amount)``.
_AMOUNT_EFFECTS = frozenset(
    {"damage", "rep_armor", "rep_shield", "neut", "nos", "cap_transfer"}
)
#: Effects measured by number of applications per bucket (EWAR). Plotted from
#: ``event_count`` (their sum_amount is meaningless / zero).
_COUNT_EFFECTS = frozenset({"scram", "disrupt", "jam"})
_KNOWN_EFFECTS = _AMOUNT_EFFECTS | _COUNT_EFFECTS

#: Stable presentation order: damage, reps, cap, then ewar.
_EFFECT_ORDER = (
    "damage",
    "rep_armor",
    "rep_shield",
    "neut",
    "nos",
    "cap_transfer",
    "scram",
    "disrupt",
    "jam",
)
#: Out drawn above the mirror baseline, in below — out sorts first.
_DIRECTION_ORDER = ("out", "in")


def _metric_for(effect_type: str) -> str:
    return "count" if effect_type in _COUNT_EFFECTS else "amount"


def _contribution(effect_type: str, sum_amount: float, event_count: int) -> float:
    """Magnitude a bucket contributes to its series."""
    if effect_type in _COUNT_EFFECTS:
        return float(event_count)
    return abs(sum_amount)


def _series_sort_key(key: str) -> tuple[int, int]:
    effect, _, direction = key.partition(":")
    e = _EFFECT_ORDER.index(effect) if effect in _EFFECT_ORDER else len(_EFFECT_ORDER)
    d = _DIRECTION_ORDER.index(direction) if direction in _DIRECTION_ORDER else len(_DIRECTION_ORDER)
    return (e, d)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _as_utc(ts: dt.datetime) -> dt.datetime:
    """Ensure *ts* is UTC-aware; SQLite reads datetimes back without tzinfo."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts


def _epoch(ts: dt.datetime) -> int:
    """Return epoch-seconds for *ts*, normalising naive datetimes to UTC first."""
    return int(_as_utc(ts).timestamp())


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


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
    """One ``(effect_type, direction)`` series aligned to the shared x axis."""

    key: str
    """Stable identity ``"{effect_type}:{direction}"`` (used for toggles)."""
    effect_type: str
    direction: str
    metric: str
    """'amount' (HP/GJ from sum_amount) or 'count' (EWAR applications)."""
    values: list[float | None]
    """Per-bucket magnitude aligned to FleetTimeline.x; None where no data."""


@dataclass
class KillEvent:
    """One kill event for the fleet timeline overlay."""

    ts: int
    """Epoch seconds of the killmail."""
    killmail_id: int
    victim_character_id: int | None
    victim_character_name: str | None
    victim_ship_name: str
    victim_ship_type_id: int | None
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
    """One entry per (effect_type, direction) with data, in presentation order."""
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
# Main analytics function
# ---------------------------------------------------------------------------


async def fleet_timeline(
    session: AsyncSession,
    br_id: str,
    our_alliance_ids: tuple[int, ...] | list[int] = (),
    our_corp_ids: tuple[int, ...] | list[int] = (),
    overrides: dict[tuple[str, int], str] | None = None,
) -> FleetTimeline:
    """Assemble a fleet-level timeline for *br_id*.

    All characters with LogEventBucket rows for the BR's fights contribute.
    Kills are classified friendly/hostile by the victim's alliance/corp against
    the baseline blues plus any per-BR FC/HC overrides (see analytics.sides_config).
    Returns empty arrays (not an error) when no buckets or kills exist.
    """
    friendly_alliances = set(our_alliance_ids)
    friendly_corps = set(our_corp_ids)
    side_overrides = overrides or {}
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

    if not fight_ids:
        return FleetTimeline(
            x=[],
            series=[],
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

    # 3. Build x-axis from contributing (known-effect, directional) buckets --------
    def _relevant(b: LogEventBucket) -> bool:
        return b.effect_type in _KNOWN_EFFECTS and b.direction in _DIRECTION_ORDER

    x_set: set[int] = {_epoch(b.bucket_ts) for b in bucket_rows if _relevant(b)}
    x: list[int] = sorted(x_set)
    x_index: dict[int, int] = {ts: i for i, ts in enumerate(x)}

    # 4. Accumulate magnitudes into per-(effect,direction) value arrays ------------
    series_values: dict[str, list[float | None]] = {}
    for b in bucket_rows:
        if not _relevant(b):
            continue
        key = f"{b.effect_type}:{b.direction}"
        arr = series_values.get(key)
        if arr is None:
            arr = [None] * len(x)
            series_values[key] = arr
        idx = x_index[_epoch(b.bucket_ts)]
        current = arr[idx]
        arr[idx] = (current or 0.0) + _contribution(b.effect_type, b.sum_amount, b.event_count)

    series: list[FleetSeriesOut] = []
    for key in sorted(series_values, key=_series_sort_key):
        effect, _, direction = key.partition(":")
        series.append(
            FleetSeriesOut(
                key=key,
                effect_type=effect,
                direction=direction,
                metric=_metric_for(effect),
                values=series_values[key],
            )
        )

    # 5. Build kills from FightKill + Killmail + FightSide + InventoryType ---------
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
        km_rows: list[Killmail] = list(
            (
                await session.execute(
                    select(Killmail).where(Killmail.killmail_id.in_(km_ids))
                )
            ).scalars()
        )
        km_map: dict[int, Killmail] = {km.killmail_id: km for km in km_rows}

        victim_ids = {
            km.victim_character_id for km in km_map.values() if km.victim_character_id is not None
        }
        victim_names: dict[int, str] = {}
        if victim_ids:
            for ch in (
                await session.execute(
                    select(Character).where(Character.character_id.in_(victim_ids))
                )
            ).scalars():
                if ch.name:
                    victim_names[ch.character_id] = ch.name

        ship_type_ids = {
            km.victim_ship_type_id for km in km_map.values() if km.victim_ship_type_id is not None
        }
        ship_name_map: dict[int, str] = {}
        if ship_type_ids:
            for inv in (
                await session.execute(
                    select(InventoryType).where(InventoryType.type_id.in_(ship_type_ids))
                )
            ).scalars():
                ship_name_map[inv.type_id] = inv.name

        for km_id in km_ids:
            km = km_map.get(km_id)
            if km is None:
                continue
            side_kind = classify_entity(
                km.victim_alliance_id,
                km.victim_corporation_id,
                baseline_alliances=friendly_alliances,
                baseline_corps=friendly_corps,
                overrides=side_overrides,
            )
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
                    victim_character_name=(
                        victim_names.get(km.victim_character_id)
                        if km.victim_character_id is not None
                        else None
                    ),
                    victim_ship_name=ship_name,
                    victim_ship_type_id=km.victim_ship_type_id,
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
