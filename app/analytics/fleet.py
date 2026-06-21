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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import classify_entity
from app.analytics.weapons import classify_weapon
from app.config import Settings, get_settings
from app.db.models import (
    BUCKET_SECONDS,
    BrFight,
    Character,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
    LogEvent,
    LogEventBucket,
)
from app.observability.logging import log
from app.roster.snapshot import get_roster_store

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
    target_ship: str | None = None
    quality: str | None = None


async def _resolve_char_names(
    session: AsyncSession, settings: Settings, char_ids: set[int]
) -> dict[int, str]:
    """Resolve character ids → names without any network call or write.

    Reads persisted ``Character`` rows (populated at ingest) first, then falls
    back to the in-memory roster snapshot for log-only roster members. Runs on
    read (GET) endpoints, so it must NOT hit ESI or commit — that would block the
    event loop and take a write lock under concurrency. Unresolved ids are left
    out (callers render ``Char {id}``).
    """
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
            roster = await get_roster_store(settings).get()
            roster_names = {
                c.character_id: c.character_name for u in roster.users for c in u.characters
            }
            for cid in missing:
                nm = roster_names.get(cid)
                if nm:
                    names[cid] = nm
        except Exception as exc:  # roster unavailable — fall back to ids
            log.warning("contributions.name_resolve_roster_failed", error=str(exc))
    return names


async def fleet_snapshot(
    session: AsyncSession,
    br_id: str,
    from_ts: int,
    to_ts: int,
    settings: Settings,
    character_id: int | None = None,
) -> list[Contribution]:
    """Break down activity in the half-open window [from_ts, to_ts) into source→target
    rows, grouped by (source, target+ship, effect, direction), sorted by value desc. Damage
    rows resolve a weapon icon and a dominant hit-quality. (from_ts > to_ts is swapped.)

    When *character_id* is given, only that character's log perspective is included
    (their outgoing + incoming events) — the per-pilot snapshot.
    """
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return []
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts
    start = dt.datetime.fromtimestamp(from_ts, tz=dt.UTC).replace(tzinfo=None)
    end = dt.datetime.fromtimestamp(to_ts, tz=dt.UTC).replace(tzinfo=None)

    conditions = [
        LogEvent.fight_id.in_(fight_ids),
        LogEvent.ts >= start,
        LogEvent.ts < end,
        LogEvent.effect_type.in_(_KNOWN_EFFECTS),
    ]
    if character_id is not None:
        conditions.append(LogEvent.character_id == character_id)

    rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.other_ship_name,
                LogEvent.effect_type,
                LogEvent.direction,
                LogEvent.amount,
                LogEvent.module_name,
                LogEvent.quality,
            ).where(*conditions)
        )
    ).all()

    Key = tuple[int | None, str, str, str, str]  # (cid, target, ship, eff, dir)
    agg: dict[Key, float] = {}
    module_dmg: dict[Key, dict[str, float]] = {}
    quality_ct: dict[Key, dict[str, int]] = {}
    for cid, other, oship, eff, direction, amount, module, quality in rows:
        key = (
            cid, _clean_target_name(other), _clean_target_name(oship), eff or "", direction or "",
        )
        contrib = 1.0 if eff in _COUNT_EFFECTS else abs(amount or 0.0)
        agg[key] = agg.get(key, 0.0) + contrib
        if eff == "damage":
            if module:
                module_dmg.setdefault(key, {})
                module_dmg[key][module] = module_dmg[key].get(module, 0.0) + abs(amount or 0.0)
            if quality:
                quality_ct.setdefault(key, {})
                quality_ct[key][quality] = quality_ct[key].get(quality, 0) + 1

    top_module: dict[Key, str] = {
        k: max(m.items(), key=lambda kv: kv[1])[0] for k, m in module_dmg.items()
    }
    wanted: set[str] = set()
    for name in top_module.values():
        wanted.add(name)
        fb = classify_weapon(name).fallback_name
        if fb:
            wanted.add(fb)
    name_to_type: dict[str, int] = {}
    if wanted:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.name.in_(wanted)))
        ).scalars():
            name_to_type[inv.name] = inv.type_id

    names = await _resolve_char_names(session, settings, {k[0] for k in agg if k[0] is not None})

    out: list[Contribution] = []
    for (cid, other, oship, eff, direction), val in agg.items():
        key = (cid, other, oship, eff, direction)
        module = top_module.get(key)
        icon_type_id: int | None = None
        category: str | None = None
        if module is not None:
            wc = classify_weapon(module)
            category = wc.category
            icon_type_id = name_to_type.get(module) or (
                name_to_type.get(wc.fallback_name) if wc.fallback_name else None
            )
        quality_label: str | None = None
        if key in quality_ct:
            quality_label = max(quality_ct[key].items(), key=lambda kv: kv[1])[0]
        out.append(
            Contribution(
                source_character_id=cid,
                source_name=(names.get(cid) or f"Char {cid}") if cid is not None else "?",
                target_name=other,
                target_ship=(oship if oship != "?" else None),
                effect_type=eff,
                direction=direction,
                group=_EFFECT_GROUP.get(eff, "other"),
                value=val,
                module_name=module,
                icon_type_id=icon_type_id,
                weapon_category=category,
                quality=quality_label,
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


def _series_sort_key(key: str) -> tuple[int, int]:
    effect, _, direction = key.partition(":")
    e = _EFFECT_ORDER.index(effect) if effect in _EFFECT_ORDER else len(_EFFECT_ORDER)
    d = (
        _DIRECTION_ORDER.index(direction)
        if direction in _DIRECTION_ORDER
        else len(_DIRECTION_ORDER)
    )
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
class LeaderEntry:
    """Top character for one metric in a single time bucket."""

    name: str
    ship: str | None
    amount: float


@dataclass
class Leaders:
    """Per-bucket leaders, split by the TARGET's side.

    'friendly' = target's alliance_id ∈ friendly_alliances (or corp_id ∈ friendly_corps).
    Characters whose side cannot be determined are treated as HOSTILE to avoid
    mislabelling non-NV pilots as friendly.
    """

    top_friendly_dmg_taken: LeaderEntry | None
    """Friendly character receiving the most incoming damage this bucket."""
    top_hostile_dmg_taken: LeaderEntry | None
    """Hostile (or unknown-side) character receiving the most incoming damage this bucket."""
    top_friendly_rep_recv: LeaderEntry | None
    """Friendly character receiving the most incoming reps this bucket."""


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
    leaders: list[Leaders]
    """Per-bucket top characters for 4 metrics, aligned index-for-index to x.
    Empty list when no log buckets exist (i.e. x is also empty)."""
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
# Leaders helpers
# ---------------------------------------------------------------------------

CAPSULE_TYPE_ID = 670


async def _resolve_char_ships(
    session: AsyncSession, fight_ids: list[int]
) -> dict[int, str]:
    """Return character_id → hull name from killmail data (capsules excluded).

    Mirrors composition.py: victim hull first, then attacker hull; capsules
    (type_id 670) are skipped. Only the first non-capsule hull per character
    is retained (sufficient for the tooltip label).
    """
    km_ids: list[int] = list(
        (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id.in_(fight_ids))
            )
        ).scalars()
    )
    if not km_ids:
        return {}

    char_ship: dict[int, int] = {}  # character_id → ship_type_id

    # Victims (authoritative hull — same as composition.py)
    for char_id, ship_id in (
        await session.execute(
            select(Killmail.victim_character_id, Killmail.victim_ship_type_id).where(
                Killmail.killmail_id.in_(km_ids)
            )
        )
    ).all():
        if char_id is None or ship_id is None or ship_id == CAPSULE_TYPE_ID:
            continue
        char_ship[char_id] = ship_id

    # Attackers (hull if not already seen from victim side)
    for char_id, ship_id in (
        await session.execute(
            select(KillmailAttacker.character_id, KillmailAttacker.ship_type_id).where(
                KillmailAttacker.killmail_id.in_(km_ids)
            )
        )
    ).all():
        if char_id is None or ship_id is None or ship_id == CAPSULE_TYPE_ID:
            continue
        char_ship.setdefault(char_id, ship_id)

    if not char_ship:
        return {}

    type_ids = set(char_ship.values())
    inv_names: dict[int, str] = {}
    for inv in (
        await session.execute(select(InventoryType).where(InventoryType.type_id.in_(type_ids)))
    ).scalars():
        inv_names[inv.type_id] = inv.name

    return {cid: inv_names[sid] for cid, sid in char_ship.items() if sid in inv_names}


async def _build_char_side_map(
    session: AsyncSession,
    fight_ids: list[int],
    friendly_alliances: set[int],
    friendly_corps: set[int],
) -> dict[int, str]:
    """Return character_id → 'friendly' | 'hostile' from killmail participants.

    Victims are checked first (authoritative hull source mirrors _resolve_char_ships).
    Attacker rows supplement missing entries.  Characters with no alliance/corp info,
    or whose alliance/corp is not in the friendly sets, are classified 'hostile' —
    this avoids ever mislabelling a non-NV pilot as friendly.
    """
    km_ids: list[int] = list(
        (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id.in_(fight_ids))
            )
        ).scalars()
    )
    if not km_ids:
        return {}

    char_side: dict[int, str] = {}

    # Victims
    for char_id, alli_id, corp_id in (
        await session.execute(
            select(
                Killmail.victim_character_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        side = (
            "friendly"
            if (alli_id is not None and alli_id in friendly_alliances)
            or (corp_id is not None and corp_id in friendly_corps)
            else "hostile"
        )
        char_side[char_id] = side

    # Attackers (fill gaps — victim entry takes precedence via setdefault)
    for char_id, alli_id, corp_id in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        side = (
            "friendly"
            if (alli_id is not None and alli_id in friendly_alliances)
            or (corp_id is not None and corp_id in friendly_corps)
            else "hostile"
        )
        char_side.setdefault(char_id, side)

    return char_side


async def _compute_leaders(
    session: AsyncSession,
    fight_ids: list[int],
    x: list[int],
    x_index: dict[int, int],
    settings: Settings,
    friendly_alliances: set[int] | None = None,
    friendly_corps: set[int] | None = None,
) -> list[Leaders]:
    """Return per-bucket Leaders aligned index-for-index to *x*.

    Returns [] (not a list of Leaders) when no log buckets exist.

    Metric classification (by TARGET side):
      FRIENDLY target, damage / in  → top_friendly_dmg_taken
      HOSTILE  target, damage / in  → top_hostile_dmg_taken
      FRIENDLY target, rep* / in    → top_friendly_rep_recv

    Characters not found in the killmail side map are treated as HOSTILE.
    """
    if not x:
        return []

    eff_friendly_alliances: set[int] = friendly_alliances or set()
    eff_friendly_corps: set[int] = friendly_corps or set()

    # Build character → side map from killmail participants.
    char_side_map = await _build_char_side_map(
        session, fight_ids, eff_friendly_alliances, eff_friendly_corps
    )

    # Fetch per-(bucket_ts, character_id, effect_type, direction) aggregates.
    rows = (
        await session.execute(
            select(
                LogEventBucket.bucket_ts,
                LogEventBucket.character_id,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
                func.sum(func.abs(LogEventBucket.sum_amount)).label("total"),
            )
            .where(
                LogEventBucket.fight_id.in_(fight_ids),
                LogEventBucket.effect_type.in_(_AMOUNT_EFFECTS),
                LogEventBucket.direction.in_(_DIRECTION_ORDER),
            )
            .group_by(
                LogEventBucket.bucket_ts,
                LogEventBucket.character_id,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
            )
        )
    ).all()

    if not rows:
        return []

    char_ids: set[int] = {char_id for _, char_id, _, _, _ in rows}
    char_names = await _resolve_char_names(session, settings, char_ids)
    char_ships = await _resolve_char_ships(session, fight_ids)

    # Three side-aware accumulators per bucket index:
    #   "friendly_dmg_taken" | "hostile_dmg_taken" | "friendly_rep_recv"
    BucketAcc = dict[str, dict[int, float]]
    bucket_acc: dict[int, BucketAcc] = {}

    for bucket_ts, char_id, effect_type, direction, total in rows:
        idx = x_index.get(_epoch(bucket_ts))
        if idx is None:
            continue
        amount = float(total or 0.0)

        # Only incoming effects matter; outgoing is dropped (we track receivers).
        if direction != "in":
            continue

        # Determine target's side; unknown → hostile (safe default).
        side = char_side_map.get(char_id, "hostile")

        if effect_type == "damage":
            metric = "friendly_dmg_taken" if side == "friendly" else "hostile_dmg_taken"
        elif effect_type.startswith("rep"):
            if side != "friendly":
                continue  # no hostile rep recv entry
            metric = "friendly_rep_recv"
        else:
            continue

        acc = bucket_acc.setdefault(idx, {})
        per_char = acc.setdefault(metric, {})
        per_char[char_id] = per_char.get(char_id, 0.0) + amount

    def _best(per_char: dict[int, float] | None) -> LeaderEntry | None:
        if not per_char:
            return None
        best_id, best_amt = max(per_char.items(), key=lambda kv: kv[1])
        name = char_names.get(best_id) or f"Char {best_id}"
        return LeaderEntry(name=name, ship=char_ships.get(best_id), amount=best_amt)

    leaders: list[Leaders] = []
    for i in range(len(x)):
        acc = bucket_acc.get(i, {})
        leaders.append(
            Leaders(
                top_friendly_dmg_taken=_best(acc.get("friendly_dmg_taken")),
                top_hostile_dmg_taken=_best(acc.get("hostile_dmg_taken")),
                top_friendly_rep_recv=_best(acc.get("friendly_rep_recv")),
            )
        )
    return leaders


# ---------------------------------------------------------------------------
# Main analytics function
# ---------------------------------------------------------------------------


async def fleet_timeline(
    session: AsyncSession,
    br_id: str,
    our_alliance_ids: tuple[int, ...] | list[int] = (),
    our_corp_ids: tuple[int, ...] | list[int] = (),
    overrides: dict[tuple[str, int], str] | None = None,
    *,
    settings: Settings | None = None,
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
            leaders=[],
            kills=[],
            fights=fights,
            bucket_seconds=BUCKET_SECONDS,
            t_start=None,
            t_end=None,
        )

    # 2. Aggregate buckets in SQL: one row per (bucket_ts, effect, direction) across
    #    all characters/fights. Amount effects use SUM(ABS(sum_amount)) (exactly the
    #    prior per-row sum-of-abs); count effects use SUM(event_count). Equivalent to
    #    the old Python loop but transfers far fewer rows and sums off the event loop.
    agg_rows = (
        await session.execute(
            select(
                LogEventBucket.bucket_ts,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
                func.sum(func.abs(LogEventBucket.sum_amount)).label("amt"),
                func.sum(LogEventBucket.event_count).label("cnt"),
            )
            .where(
                LogEventBucket.fight_id.in_(fight_ids),
                LogEventBucket.effect_type.in_(_KNOWN_EFFECTS),
                LogEventBucket.direction.in_(_DIRECTION_ORDER),
            )
            .group_by(
                LogEventBucket.bucket_ts,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
            )
        )
    ).all()

    # 3. Build x-axis from contributing buckets ------------------------------------
    x_set: set[int] = {_epoch(bucket_ts) for bucket_ts, _e, _d, _a, _c in agg_rows}
    x: list[int] = sorted(x_set)
    x_index: dict[int, int] = {ts: i for i, ts in enumerate(x)}

    # 4. Place per-(effect,direction) magnitudes into value arrays ------------------
    series_values: dict[str, list[float | None]] = {}
    for bucket_ts, effect, direction, amt, cnt in agg_rows:
        key = f"{effect}:{direction}"
        arr = series_values.get(key)
        if arr is None:
            arr = [None] * len(x)
            series_values[key] = arr
        idx = x_index[_epoch(bucket_ts)]
        value = float(cnt) if effect in _COUNT_EFFECTS else float(amt or 0.0)
        arr[idx] = (arr[idx] or 0.0) + value

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

    leaders = await _compute_leaders(
        session, fight_ids, x, x_index, settings or get_settings(),
        friendly_alliances=friendly_alliances,
        friendly_corps=friendly_corps,
    )

    return FleetTimeline(
        x=x,
        series=series,
        leaders=leaders,
        kills=kills,
        fights=fights,
        bucket_seconds=BUCKET_SECONDS,
        t_start=x[0] if x else None,
        t_end=x[-1] if x else None,
    )
