"""Fleet-broadcast analytics: join broadcasts to combat events for fleet/pilot stats.

Reads ``Broadcast`` rows (broadcast_associate stamped their ``fight_id``) and the
existing ``LogEvent`` / ``Killmail`` data.  Never re-parses or mutates anything.

Attribution asymmetry (see app/db/models.py::LogEvent):

* damage / reps / cap are **owner-relative** — keyed by ``character_id`` + ``direction``
  + ``other_name``; they do NOT populate source_name/target_name.
* tackle (scram/disrupt) uses resolved ``source_name`` / ``target_name`` and must
  exclude ``dedupe_suppressed`` observations.

So "fleet fires on a called enemy" = outgoing damage whose ``other_name`` is the enemy,
or a scram/disrupt whose ``target_name`` is the enemy; while "pilot X received reps" =
LogEvents with ``character_id == X`` and ``direction == 'in'`` (X's own log).
"""

from __future__ import annotations

import bisect
import datetime as dt
import re
import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BrFight,
    Broadcast,
    BroadcastFile,
    Character,
    FightKill,
    InventoryType,
    Killmail,
    LogEvent,
)

# ---------------------------------------------------------------------------
# Tunable thresholds (seconds).  Kept as module constants like BUCKET_SECONDS.
# ---------------------------------------------------------------------------
# The outlier cap: a fleet fire / logi rep more than this long after a broadcast is
# NOT a real response — it's coincidence (a same-named target re-engaged minutes later,
# a rep that happened to land much later, or a logi ship that never follows a target
# call at all). Beyond it the broadcast is treated as unanswered rather than counted as
# an enormous response time that skews medians and over-penalises pilots.
MATCH_WINDOW_S: int = 20
COMPLIANCE_WINDOW_S: int = 10  # a target call is "complied with" if fired on within this
JUSTIFICATION_WINDOW_S: int = 10  # incoming damage within ±this justifies a rep broadcast
REACTION_LOOKBACK_S: int = 20  # look back this far for the damage burst preceding a call
LATE_BROADCAST_S: int = 5  # broadcast<->death gap below this = "too late to save"

# Broadcast lead time: how long before the threatening damage burst a pilot called for
# shield/armor. A "burst" is >= MEANINGFUL_DAMAGE_HP of incoming damage within
# BURST_WINDOW_S; the lead is broadcast -> burst onset (positive = proactive warning,
# <= 0 = the burst was already landing, so logi can't save them). Searched within
# [broadcast - LEAD_LOOKBACK_S, broadcast + LEAD_LOOKAHEAD_S]. The HP threshold is
# fleet-dependent and tunable.
MEANINGFUL_DAMAGE_HP: float = 5000.0
BURST_WINDOW_S: int = 3
LEAD_LOOKBACK_S: int = 10
LEAD_LOOKAHEAD_S: int = 20

CAPSULE_TYPE_ID = 670  # pod deaths excluded from the ship-loss death signal

_TAG_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")
_REP_TYPES = ("rep_shield", "rep_armor", "cap_transfer")
_FRIENDLY_KINDS = frozenset({"needs_shield", "needs_armor", "needs_capacitor", "repair"})
_RESOURCE = {
    "needs_shield": "shield",
    "needs_armor": "armor",
    "needs_capacitor": "capacitor",
    "repair": "repair",
}


def _clean(name: str | None) -> str:
    if not name:
        return ""
    s = _TAG_RE.sub("", name)
    return re.sub(r"\s{2,}", " ", s).strip().lower()


def _naive(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _utc(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=dt.UTC) if ts.tzinfo is None else ts


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TargetCallRow:
    broadcast_id: int
    ts: dt.datetime
    subject_name: str
    subject_ship: str | None
    fight_id: int | None
    first_fire_delta_s: float | None  # None = never fired on after the call
    already_primaried: bool  # fleet was already firing before the call
    complied: bool  # fired on within COMPLIANCE_WINDOW_S


@dataclass
class PilotSwitchRow:
    character_id: int
    character_name: str
    calls_fired: int  # calls this pilot landed a shot on after the call
    median_switch_s: float | None
    compliance_rate: float  # of calls_fired, fraction within COMPLIANCE_WINDOW_S


@dataclass
class TargetCallMetrics:
    rows: list[TargetCallRow] = field(default_factory=list)
    per_pilot: list[PilotSwitchRow] = field(default_factory=list)
    compliance_rate: float | None = None
    unanswered_count: int = 0


@dataclass
class RepRequestRow:
    broadcast_id: int
    ts: dt.datetime
    subject_name: str
    subject_character_id: int | None
    resource: str
    fight_id: int | None
    logi_response_s: float | None  # broadcast -> first rep received (<= MATCH_WINDOW_S)
    repper_name: str | None  # the logi pilot who applied that first rep
    justified: bool | None  # incoming damage around the call; None = pilot has no log
    reaction_s: float | None  # damage onset -> broadcast
    # broadcast -> onset of the threatening damage burst (shield/armor only). Positive =
    # proactive warning; <= 0 = the burst was already landing (too late to save). None =
    # no meaningful burst near the call.
    damage_lead_s: float | None
    broadcast_late: bool  # the burst was already underway at the broadcast
    has_log: bool


@dataclass
class RepRequestMetrics:
    rows: list[RepRequestRow] = field(default_factory=list)
    false_broadcast_rate: float | None = None
    median_logi_response_s: float | None = None
    median_damage_lead_s: float | None = None
    late_broadcast_rate: float | None = None
    unresolved_subjects: int = 0


@dataclass
class DeathBroadcastRow:
    character_id: int
    character_name: str
    ship: str | None
    killmail_id: int
    ts: dt.datetime
    fight_id: int | None
    classification: str  # no_broadcast | late_broadcast | unanswered | ok
    last_broadcast_delta_s: float | None  # broadcast -> death seconds


@dataclass
class QualityReport:
    total_targets: int = 0
    total_reps: int = 0
    unanswered_targets: int = 0
    false_reps: int = 0
    late_broadcasts: int = 0  # shield/armor calls with the damage burst already underway
    deaths_without_broadcast: int = 0
    unresolved_rep_subjects: int = 0


@dataclass
class BroadcastSummary:
    n_targets: int = 0
    n_reps: int = 0
    median_time_to_fire_s: float | None = None
    compliance_rate: float | None = None
    median_logi_response_s: float | None = None
    median_reaction_s: float | None = None
    median_damage_lead_s: float | None = None
    late_broadcast_rate: float | None = None
    false_broadcast_rate: float | None = None
    deaths_total: int = 0
    deaths_flagged: int = 0  # late_broadcast | no_broadcast | unanswered


@dataclass
class BroadcastMetrics:
    has_broadcasts: bool = False
    summary: BroadcastSummary = field(default_factory=BroadcastSummary)
    targets: TargetCallMetrics = field(default_factory=TargetCallMetrics)
    reps: RepRequestMetrics = field(default_factory=RepRequestMetrics)
    quality: QualityReport = field(default_factory=QualityReport)
    deaths: list[DeathBroadcastRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading context
# ---------------------------------------------------------------------------


@dataclass
class _BcRow:
    broadcast_id: int
    fight_id: int | None
    ts: dt.datetime
    kind: str
    subject_name: str
    subject_ship: str | None
    subject_cid: int | None
    subject_clean: str


@dataclass
class _Ctx:
    broadcasts: list[_BcRow]
    fight_ids: list[int]
    # (fight_id, clean_enemy_name) -> sorted [(ts, character_id)] of fleet fire
    fire_index: dict[tuple[int, str], list[tuple[dt.datetime, int]]]
    # cid -> sorted (ts, fight, amount) of incoming damage
    in_dmg: dict[int, list[tuple[dt.datetime, int | None, float]]]
    # cid -> sorted (ts, fight, repper_name) of remote reps received
    in_rep: dict[int, list[tuple[dt.datetime, int | None, str | None]]]
    log_owner_cids: set[int]
    names: dict[int, str]
    deaths: list[tuple[int, dt.datetime, int | None, int, str | None]]
    # ^ (cid, death_ts, fight_id, killmail_id, ship_name)


async def _load_ctx(session: AsyncSession, br_id: str) -> _Ctx:
    fight_ids = [
        fid
        for fid in (
            await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))
        ).scalars()
    ]

    # Broadcasts from the canonical (non-superseded) file only.
    bc_rows_raw = (
        await session.execute(
            select(
                Broadcast.broadcast_id,
                Broadcast.fight_id,
                Broadcast.ts,
                Broadcast.kind,
                Broadcast.subject_name,
                Broadcast.subject_ship,
                Broadcast.subject_character_id,
            )
            .join(BroadcastFile, BroadcastFile.broadcast_file_id == Broadcast.file_id)
            .where(Broadcast.br_id == br_id)
            .where(BroadcastFile.superseded.is_(False))
            .order_by(Broadcast.ts)
        )
    ).all()
    broadcasts = [
        _BcRow(
            broadcast_id=bid,
            fight_id=fid,
            ts=_naive(ts),
            kind=kind,
            subject_name=sname,
            subject_ship=sship,
            subject_cid=scid,
            subject_clean=_clean(sname),
        )
        for (bid, fid, ts, kind, sname, sship, scid) in bc_rows_raw
    ]

    called_names = {b.subject_name for b in broadcasts if b.kind == "target"}
    rep_subject_cids = {
        b.subject_cid for b in broadcasts if b.kind in _FRIENDLY_KINDS and b.subject_cid
    }

    fire_index: dict[tuple[int, str], list[tuple[dt.datetime, int]]] = {}
    if fight_ids and called_names:
        fire_rows = (
            await session.execute(
                select(
                    LogEvent.fight_id,
                    LogEvent.ts,
                    LogEvent.character_id,
                    LogEvent.effect_type,
                    LogEvent.other_name,
                    LogEvent.target_name,
                )
                .where(LogEvent.fight_id.in_(fight_ids))
                .where(LogEvent.direction == "out")
                .where(
                    (
                        (LogEvent.effect_type == "damage")
                        & (LogEvent.other_name.in_(called_names))
                    )
                    | (
                        LogEvent.effect_type.in_(["scram", "disrupt"])
                        & LogEvent.dedupe_suppressed.is_(False)
                        & (LogEvent.target_name.in_(called_names))
                    )
                )
            )
        ).all()
        for fid, ts, cid, eff, other, target in fire_rows:
            if fid is None or cid is None:
                continue
            name = _clean(other if eff == "damage" else target)
            if not name:
                continue
            fire_index.setdefault((fid, name), []).append((_naive(ts), int(cid)))
        for key in fire_index:
            fire_index[key].sort(key=lambda t: t[0])

    # Log owners present in this BR (for rep justification "unknown" handling & friendliness).
    log_owner_cids: set[int] = set()
    if fight_ids:
        log_owner_cids = {
            int(cid)
            for cid in (
                await session.execute(
                    select(LogEvent.character_id)
                    .where(LogEvent.fight_id.in_(fight_ids))
                    .where(LogEvent.character_id.is_not(None))
                    .distinct()
                )
            ).scalars()
            if cid is not None
        }

    friendly_cids = set(rep_subject_cids) | log_owner_cids

    # in_dmg carries the hit amount for burst detection (broadcast lead time).
    in_dmg: dict[int, list[tuple[dt.datetime, int | None, float]]] = {}
    # in_rep carries the repper name (other_name on the owner-relative rep-in event) so
    # the logi response can be attributed to the pilot who actually applied the rep.
    in_rep: dict[int, list[tuple[dt.datetime, int | None, str | None]]] = {}
    if fight_ids and friendly_cids:
        in_rows = (
            await session.execute(
                select(
                    LogEvent.character_id,
                    LogEvent.ts,
                    LogEvent.fight_id,
                    LogEvent.effect_type,
                    LogEvent.other_name,
                    LogEvent.amount,
                )
                .where(LogEvent.fight_id.in_(fight_ids))
                .where(LogEvent.direction == "in")
                .where(LogEvent.character_id.in_(friendly_cids))
                .where(LogEvent.effect_type.in_(["damage", *_REP_TYPES]))
            )
        ).all()
        for cid, ts, fid, eff, other, amount in in_rows:
            if cid is None:
                continue
            if eff == "damage":
                in_dmg.setdefault(int(cid), []).append((_naive(ts), fid, float(amount or 0.0)))
            else:
                in_rep.setdefault(int(cid), []).append((_naive(ts), fid, other))
        for cid in in_dmg:
            in_dmg[cid].sort(key=lambda t: t[0])
        for cid in in_rep:
            in_rep[cid].sort(key=lambda t: t[0])

    # Friendly ship-loss deaths (exclude capsules) in this BR's fights.
    deaths: list[tuple[int, dt.datetime, int | None, int, str | None]] = []
    if fight_ids and friendly_cids:
        death_rows = (
            await session.execute(
                select(
                    Killmail.victim_character_id,
                    Killmail.killmail_time,
                    FightKill.fight_id,
                    Killmail.killmail_id,
                    InventoryType.name,
                )
                .join(FightKill, FightKill.killmail_id == Killmail.killmail_id)
                .join(
                    InventoryType,
                    InventoryType.type_id == Killmail.victim_ship_type_id,
                    isouter=True,
                )
                .where(FightKill.fight_id.in_(fight_ids))
                .where(Killmail.victim_character_id.in_(friendly_cids))
                .where(Killmail.victim_ship_type_id != CAPSULE_TYPE_ID)
            )
        ).all()
        for cid, kt, fid, kmid, ship in death_rows:
            deaths.append((int(cid), _naive(kt), fid, int(kmid), ship))

    # Display names for everyone referenced.
    ref_cids = (
        set(log_owner_cids)
        | {b.subject_cid for b in broadcasts if b.subject_cid}
        | {cid for (_k, lst) in fire_index.items() for (_ts, cid) in lst}
        | {d[0] for d in deaths}
    )
    names: dict[int, str] = {}
    if ref_cids:
        for cid, nm in (
            await session.execute(
                select(Character.character_id, Character.name).where(
                    Character.character_id.in_(ref_cids)
                )
            )
        ).all():
            if nm:
                names[int(cid)] = nm

    return _Ctx(
        broadcasts=broadcasts,
        fight_ids=fight_ids,
        fire_index=fire_index,
        in_dmg=in_dmg,
        in_rep=in_rep,
        log_owner_cids=log_owner_cids,
        names=names,
        deaths=deaths,
    )


# ---------------------------------------------------------------------------
# Metric computation (pure over the loaded context)
# ---------------------------------------------------------------------------


def _fires_after(
    fires: list[tuple[dt.datetime, int]], b_ts: dt.datetime
) -> tuple[dt.datetime | None, bool, dict[int, dt.datetime]]:
    """Return (first_fire_at_or_after, already_primaried, per_cid_first_after)."""
    if not fires:
        return None, False, {}
    keys = [f[0] for f in fires]
    idx = bisect.bisect_left(keys, b_ts)
    already = idx > 0  # some fire strictly before b_ts
    first_after = fires[idx][0] if idx < len(fires) else None
    per_cid: dict[int, dt.datetime] = {}
    for ts, cid in fires[idx:]:
        if cid not in per_cid:
            per_cid[cid] = ts
    return first_after, already, per_cid


def _compute_targets(ctx: _Ctx) -> TargetCallMetrics:
    rows: list[TargetCallRow] = []
    # per pilot: list of switch deltas (one per answered call)
    pilot_deltas: dict[int, list[float]] = {}
    complied_total = 0
    unanswered = 0
    calls = [b for b in ctx.broadcasts if b.kind == "target"]
    for b in calls:
        fires = ctx.fire_index.get((b.fight_id, b.subject_clean), []) if b.fight_id else []
        first_after, already, per_cid = _fires_after(fires, b.ts)
        delta = (first_after - b.ts).total_seconds() if first_after else None
        # Outlier cap: a first fire beyond the match window is not a real response.
        if delta is not None and delta > MATCH_WINDOW_S:
            delta = None
        complied = delta is not None and delta <= COMPLIANCE_WINDOW_S
        if delta is None:
            unanswered += 1
        if complied:
            complied_total += 1
        rows.append(
            TargetCallRow(
                broadcast_id=b.broadcast_id,
                ts=_utc(b.ts),
                subject_name=b.subject_name,
                subject_ship=b.subject_ship,
                fight_id=b.fight_id,
                first_fire_delta_s=round(delta, 2) if delta is not None else None,
                already_primaried=already,
                complied=complied,
            )
        )
        for cid, ts in per_cid.items():
            d = (ts - b.ts).total_seconds()
            # Only within-window switches count — a pilot who never engages the called
            # target within the window (e.g. logistics) is not scored on it at all.
            if d <= MATCH_WINDOW_S:
                pilot_deltas.setdefault(cid, []).append(d)

    per_pilot: list[PilotSwitchRow] = []
    for cid, deltas in pilot_deltas.items():
        within = [d for d in deltas if d <= COMPLIANCE_WINDOW_S]
        per_pilot.append(
            PilotSwitchRow(
                character_id=cid,
                character_name=ctx.names.get(cid, f"Char {cid}"),
                calls_fired=len(deltas),
                median_switch_s=_median(deltas),
                compliance_rate=round(len(within) / len(deltas), 3) if deltas else 0.0,
            )
        )
    per_pilot.sort(key=lambda r: (-r.calls_fired, r.median_switch_s or 1e9))

    compliance_rate = round(complied_total / len(calls), 3) if calls else None
    return TargetCallMetrics(
        rows=rows,
        per_pilot=per_pilot,
        compliance_rate=compliance_rate,
        unanswered_count=unanswered,
    )


def _first_after(
    events: list[tuple[dt.datetime, int | None, str | None]],
    b_ts: dt.datetime,
    fight_id: int | None,
) -> dt.datetime | None:
    """First event timestamp at/after b_ts in the same fight (events are (ts, fid, ...))."""
    best: dt.datetime | None = None
    for ev in events:
        ts, fid = ev[0], ev[1]
        if ts < b_ts:
            continue
        if fight_id is not None and fid is not None and fid != fight_id:
            continue
        if best is None or ts < best:
            best = ts
    return best


def _first_rep_after(
    events: list[tuple[dt.datetime, int | None, str | None]],
    b_ts: dt.datetime,
    fight_id: int | None,
    window_s: int,
) -> tuple[float, str | None] | None:
    """First rep applied within [b_ts, b_ts+window]; returns (delay_s, repper_name)."""
    best_ts: dt.datetime | None = None
    best_src: str | None = None
    for ts, fid, src in events:
        if ts < b_ts or (ts - b_ts).total_seconds() > window_s:
            continue
        if fight_id is not None and fid is not None and fid != fight_id:
            continue
        if best_ts is None or ts < best_ts:
            best_ts, best_src = ts, src
    if best_ts is None:
        return None
    return round((best_ts - b_ts).total_seconds(), 2), best_src


def _burst_lead(
    dmg: list[tuple[dt.datetime, float]], b_ts: dt.datetime
) -> tuple[float | None, bool]:
    """Broadcast timing relative to the onset of the threatening damage burst.

    ``dmg`` is the pilot's incoming (ts, amount), already fight-scoped. A burst is the
    first hit from which >= MEANINGFUL_DAMAGE_HP lands within BURST_WINDOW_S, searched in
    [b_ts - LEAD_LOOKBACK_S, b_ts + LEAD_LOOKAHEAD_S]. Returns (lead_s, late) where lead =
    broadcast - onset (the "T-minus" convention): NEGATIVE means the call came *before* the
    burst (early/proactive — earlier is better), POSITIVE means the burst was already
    landing (late, so logi cannot save them). ``late`` = lead >= 0. Returns (None, False)
    when no meaningful burst is near the call.
    """
    lo = b_ts - dt.timedelta(seconds=LEAD_LOOKBACK_S)
    hi = b_ts + dt.timedelta(seconds=LEAD_LOOKAHEAD_S)
    window = [(ts, amt) for ts, amt in dmg if lo <= ts <= hi]
    onset: dt.datetime | None = None
    for i, (ts_i, _) in enumerate(window):
        total = sum(
            amt
            for ts_j, amt in window[i:]
            if (ts_j - ts_i).total_seconds() < BURST_WINDOW_S
        )
        if total >= MEANINGFUL_DAMAGE_HP:
            onset = ts_i
            break
    if onset is None:
        return None, False
    lead = round((b_ts - onset).total_seconds(), 2)
    return lead, lead >= 0


def _compute_reps(ctx: _Ctx) -> RepRequestMetrics:
    rows: list[RepRequestRow] = []
    logi_deltas: list[float] = []
    lead_values: list[float] = []
    false_count = 0
    judged = 0
    late_count = 0
    lead_judged = 0
    unresolved = 0
    reps = [b for b in ctx.broadcasts if b.kind in _FRIENDLY_KINDS]
    for b in reps:
        cid = b.subject_cid
        if cid is None:
            unresolved += 1
            rows.append(
                RepRequestRow(
                    broadcast_id=b.broadcast_id,
                    ts=_utc(b.ts),
                    subject_name=b.subject_name,
                    subject_character_id=None,
                    resource=_RESOURCE.get(b.kind, "shield"),
                    fight_id=b.fight_id,
                    logi_response_s=None,
                    repper_name=None,
                    justified=None,
                    reaction_s=None,
                    damage_lead_s=None,
                    broadcast_late=False,
                    has_log=False,
                )
            )
            continue
        has_log = cid in ctx.log_owner_cids
        # (a) logi response — the first rep applied within MATCH_WINDOW_S and who applied
        # it. Beyond the window there was effectively no response (not a huge delay).
        rep = _first_rep_after(ctx.in_rep.get(cid, []), b.ts, b.fight_id, MATCH_WINDOW_S)
        logi = rep[0] if rep else None
        repper = rep[1] if rep else None
        if logi is not None:
            logi_deltas.append(logi)
        # (b) justification + (c) reaction from incoming damage
        justified: bool | None = None
        reaction: float | None = None
        damage_lead: float | None = None
        late = False
        if has_log:
            dmg = [
                (ts, amt)
                for ts, fid, amt in ctx.in_dmg.get(cid, [])
                if b.fight_id is None or fid is None or fid == b.fight_id
            ]
            around = [
                ts for ts, _ in dmg if abs((ts - b.ts).total_seconds()) <= JUSTIFICATION_WINDOW_S
            ]
            # Only shield/armor calls are judged against incoming damage. A capacitor
            # or generic repair request is about neuts / a dry cap / hull, so "no damage
            # nearby" says nothing about whether it was warranted — leave it unjudged
            # rather than count it against the pilot as a false broadcast.
            if b.kind in ("needs_shield", "needs_armor"):
                justified = bool(around)
                judged += 1
                if not justified:
                    false_count += 1
            onset = [
                ts for ts, _ in dmg if 0 <= (b.ts - ts).total_seconds() <= REACTION_LOOKBACK_S
            ]
            if onset:
                reaction = round((b.ts - min(onset)).total_seconds(), 2)
            # (d) broadcast lead time — only for shield/armor requests.
            if b.kind in ("needs_shield", "needs_armor"):
                damage_lead, late = _burst_lead(dmg, b.ts)
                if damage_lead is not None:
                    lead_values.append(damage_lead)
                    lead_judged += 1
                    if late:
                        late_count += 1
        rows.append(
            RepRequestRow(
                broadcast_id=b.broadcast_id,
                ts=_utc(b.ts),
                subject_name=b.subject_name,
                subject_character_id=cid,
                resource=_RESOURCE.get(b.kind, "shield"),
                fight_id=b.fight_id,
                logi_response_s=logi,
                repper_name=repper,
                justified=justified,
                reaction_s=reaction,
                damage_lead_s=damage_lead,
                broadcast_late=late,
                has_log=has_log,
            )
        )
    return RepRequestMetrics(
        rows=rows,
        false_broadcast_rate=round(false_count / judged, 3) if judged else None,
        median_logi_response_s=_median(logi_deltas),
        median_damage_lead_s=_median(lead_values),
        late_broadcast_rate=round(late_count / lead_judged, 3) if lead_judged else None,
        unresolved_subjects=unresolved,
    )


def _compute_deaths(ctx: _Ctx) -> list[DeathBroadcastRow]:
    # Broadcasts about a given friendly pilot (rep requests + explicit repair calls).
    by_cid: dict[int, list[dt.datetime]] = {}
    for b in ctx.broadcasts:
        if b.kind in _FRIENDLY_KINDS and b.subject_cid is not None:
            by_cid.setdefault(b.subject_cid, []).append(b.ts)
    for cid in by_cid:
        by_cid[cid].sort()

    out: list[DeathBroadcastRow] = []
    for cid, death_ts, fid, kmid, ship in ctx.deaths:
        prior = [t for t in by_cid.get(cid, []) if t <= death_ts]
        if not prior:
            classification = "no_broadcast"
            delta = None
        else:
            last_bc = max(prior)
            delta = (death_ts - last_bc).total_seconds()
            if delta < LATE_BROADCAST_S:
                classification = "late_broadcast"
            else:
                repped = _first_after(ctx.in_rep.get(cid, []), last_bc, fid)
                if repped is not None and repped <= death_ts:
                    classification = "ok"
                else:
                    classification = "unanswered"
        out.append(
            DeathBroadcastRow(
                character_id=cid,
                character_name=ctx.names.get(cid, f"Char {cid}"),
                ship=ship,
                killmail_id=kmid,
                ts=_utc(death_ts),
                fight_id=fid,
                classification=classification,
                last_broadcast_delta_s=round(delta, 2) if delta is not None else None,
            )
        )
    out.sort(key=lambda r: r.ts)
    return out


_FLAGGED = frozenset({"no_broadcast", "late_broadcast", "unanswered"})


def _compute(ctx: _Ctx) -> BroadcastMetrics:
    targets = _compute_targets(ctx)
    reps = _compute_reps(ctx)
    deaths = _compute_deaths(ctx)

    quality = QualityReport(
        total_targets=len(targets.rows),
        total_reps=len(reps.rows),
        unanswered_targets=targets.unanswered_count,
        false_reps=sum(1 for r in reps.rows if r.justified is False),
        late_broadcasts=sum(1 for r in reps.rows if r.broadcast_late),
        deaths_without_broadcast=sum(1 for d in deaths if d.classification == "no_broadcast"),
        unresolved_rep_subjects=reps.unresolved_subjects,
    )

    fire_deltas = [
        r.first_fire_delta_s for r in targets.rows if r.first_fire_delta_s is not None
    ]
    reaction_vals = [r.reaction_s for r in reps.rows if r.reaction_s is not None]
    summary = BroadcastSummary(
        n_targets=len(targets.rows),
        n_reps=len(reps.rows),
        median_time_to_fire_s=_median([d for d in fire_deltas if d >= 0]),
        compliance_rate=targets.compliance_rate,
        median_logi_response_s=reps.median_logi_response_s,
        median_reaction_s=_median(reaction_vals),
        median_damage_lead_s=reps.median_damage_lead_s,
        late_broadcast_rate=reps.late_broadcast_rate,
        false_broadcast_rate=reps.false_broadcast_rate,
        deaths_total=len(deaths),
        deaths_flagged=sum(1 for d in deaths if d.classification in _FLAGGED),
    )

    return BroadcastMetrics(
        has_broadcasts=bool(ctx.broadcasts),
        summary=summary,
        targets=targets,
        reps=reps,
        quality=quality,
        deaths=deaths,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_broadcast_metrics(session: AsyncSession, br_id: str) -> BroadcastMetrics:
    """All fleet-broadcast metrics for *br_id* in one pass (loads the context once).

    Returns an empty (``has_broadcasts=False``) result — never raises — when the BR
    has no broadcasts.
    """
    ctx = await _load_ctx(session, br_id)
    if not ctx.broadcasts:
        return BroadcastMetrics(has_broadcasts=False)
    return _compute(ctx)


async def target_call_metrics(session: AsyncSession, br_id: str) -> TargetCallMetrics:
    return _compute_targets(await _load_ctx(session, br_id))


async def rep_request_metrics(session: AsyncSession, br_id: str) -> RepRequestMetrics:
    return _compute_reps(await _load_ctx(session, br_id))


async def death_broadcast_classification(
    session: AsyncSession, br_id: str
) -> list[DeathBroadcastRow]:
    return _compute_deaths(await _load_ctx(session, br_id))


async def broadcast_summary(session: AsyncSession, br_id: str) -> BroadcastSummary:
    return (await compute_broadcast_metrics(session, br_id)).summary
