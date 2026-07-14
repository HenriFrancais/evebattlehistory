"""Per-character battle performance, access-aware.

Combines the existing friendly-side composition (damage dealt, HP repaired, kills-on)
with the fleet-broadcast per-character stats (target-switch discipline, logi response,
false broadcasts, flagged deaths), plus anonymous fleet distributions so a pilot can see
where they sit without exposing other pilots' names.

Access rule (enforced by the caller passing ``elevated`` + ``viewer_character_ids``):
* aggregate ``summary`` and the anonymous ``distributions`` are public;
* named per-character rows are returned only for the viewer's own characters, unless
  the viewer is elevated (FC / High Command), who sees every friendly pilot.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.broadcasts import (
    BroadcastMetrics,
    BroadcastSummary,
    compute_broadcast_metrics,
)
from app.analytics.composition import CompositionPilot, fleet_composition
from app.analytics.sides_config import load_overrides
from app.config import AppConfig, Settings

_TAG_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")


def _clean(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"\s{2,}", " ", _TAG_RE.sub("", name)).strip().lower()


@dataclass
class PerfCharRow:
    character_id: int
    character_name: str  # the specific (alt) character
    user_name: str | None  # the owning user / main identity
    is_self: bool
    # From composition (killmail + logs)
    damage_done: float
    reps_out: float
    kills_on: int
    has_logs: bool
    # From broadcasts
    target_calls_engaged: int
    target_median_switch_s: float | None
    target_compliance_rate: float | None
    logi_response_median_s: float | None
    damage_lead_median_s: float | None  # median warning time on shield/armor calls
    late_broadcasts: int  # shield/armor calls with the burst already underway
    rep_broadcasts: int
    false_broadcasts: int
    deaths: int
    deaths_flagged: int


@dataclass
class FleetDistributions:
    """Anonymous per-pilot medians powering the comparison charts (no names).

    Each list holds one value per friendly pilot who has that metric, so a viewer's
    own median can be marked against where the rest of the fleet sits.
    """

    target_switch_s: list[float] = field(default_factory=list)
    logi_response_s: list[float] = field(default_factory=list)
    damage_lead_s: list[float] = field(default_factory=list)


@dataclass
class BrPerformance:
    elevated: bool
    has_broadcasts: bool
    summary: BroadcastSummary
    distributions: FleetDistributions
    characters: list[PerfCharRow] = field(default_factory=list)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def assemble_performance(
    friendly_pilots: list[CompositionPilot],
    m: BroadcastMetrics,
    viewer_character_ids: set[int],
    elevated: bool,
) -> BrPerformance:
    """Pure assembler: join friendly composition rows with broadcast per-character stats."""
    # Index broadcast per-character aggregates.
    switch = {p.character_id: p for p in m.targets.per_pilot}
    reps_count: dict[int, int] = {}
    false_count: dict[int, int] = {}
    lead_by_char: dict[int, list[float]] = {}  # broadcaster -> warning times
    late_by_char: dict[int, int] = {}
    for r in m.reps.rows:
        cid = r.subject_character_id
        if cid is None:
            continue
        reps_count[cid] = reps_count.get(cid, 0) + 1
        if r.justified is False:
            false_count[cid] = false_count.get(cid, 0) + 1
        if r.damage_lead_s is not None:
            lead_by_char.setdefault(cid, []).append(r.damage_lead_s)
        if r.broadcast_late:
            late_by_char[cid] = late_by_char.get(cid, 0) + 1

    # Logi response is attributed to the pilot who APPLIED the rep (repper_name),
    # resolved to a friendly character — so it is a logi-performance metric, not a
    # frontline "how fast was I repped" one.
    name_to_cid = {_clean(p.character_name): p.character_id for p in friendly_pilots}
    logi_by_char: dict[int, list[float]] = {}
    for r in m.reps.rows:
        if r.logi_response_s is None or not r.repper_name:
            continue
        repper_cid = name_to_cid.get(_clean(r.repper_name))
        if repper_cid is not None:
            logi_by_char.setdefault(repper_cid, []).append(r.logi_response_s)
    deaths_count: dict[int, int] = {}
    deaths_flagged: dict[int, int] = {}
    for d in m.deaths:
        deaths_count[d.character_id] = deaths_count.get(d.character_id, 0) + 1
        if d.classification != "ok":
            deaths_flagged[d.character_id] = deaths_flagged.get(d.character_id, 0) + 1

    # One row per character — composition emits a row per ship flown (reships), which
    # would otherwise duplicate a pilot and skew the distributions. Per-character stats
    # (damage/reps/kills) are already whole-character totals, so keep the first.
    seen: set[int] = set()
    unique_pilots: list[CompositionPilot] = []
    for p in friendly_pilots:
        if p.character_id not in seen:
            seen.add(p.character_id)
            unique_pilots.append(p)

    rows: list[PerfCharRow] = []
    for p in unique_pilots:
        cid = p.character_id
        sw = switch.get(cid)
        rows.append(
            PerfCharRow(
                character_id=cid,
                character_name=p.character_name,
                user_name=p.user_name,
                is_self=cid in viewer_character_ids,
                damage_done=float(p.damage_done),
                reps_out=float(p.reps_out),
                kills_on=int(p.kill_count),
                has_logs=p.has_logs,
                target_calls_engaged=sw.calls_fired if sw else 0,
                target_median_switch_s=sw.median_switch_s if sw else None,
                target_compliance_rate=sw.compliance_rate if sw else None,
                logi_response_median_s=_median(logi_by_char.get(cid, [])),
                damage_lead_median_s=_median(lead_by_char.get(cid, [])),
                late_broadcasts=late_by_char.get(cid, 0),
                rep_broadcasts=reps_count.get(cid, 0),
                false_broadcasts=false_count.get(cid, 0),
                deaths=deaths_count.get(cid, 0),
                deaths_flagged=deaths_flagged.get(cid, 0),
            )
        )

    # Anonymous per-pilot median distributions — computed from ALL friendly rows
    # (before the self-only filter) so a viewer can see where they sit vs the fleet.
    # Role-scoped: a pilot only appears in the target-switch spread if they actually
    # deal damage (DPS), and in the logi-response spread if they actually apply reps
    # (logi) — so DPS aren't compared on logi timing and logi aren't compared on target
    # switching.
    distributions = FleetDistributions(
        target_switch_s=[
            r.target_median_switch_s
            for r in rows
            if r.target_median_switch_s is not None and r.damage_done > 0
        ],
        logi_response_s=[
            r.logi_response_median_s
            for r in rows
            if r.logi_response_median_s is not None and r.reps_out > 0
        ],
        # Broadcast lead is universal — any pilot who calls for shield/armor.
        damage_lead_s=[
            r.damage_lead_median_s for r in rows if r.damage_lead_median_s is not None
        ],
    )

    if not elevated:
        rows = [r for r in rows if r.is_self]
    rows.sort(key=lambda r: (-r.damage_done, r.character_name.lower()))

    return BrPerformance(
        elevated=elevated,
        has_broadcasts=m.has_broadcasts,
        summary=m.summary,
        distributions=distributions,
        characters=rows,
    )


async def compute_br_performance(
    session: AsyncSession,
    br_id: str,
    *,
    viewer_character_ids: set[int],
    elevated: bool,
    cfg: AppConfig,
    settings: Settings,
    char_to_user: dict[int, str] | None = None,
) -> BrPerformance:
    """Assemble access-filtered per-character performance for *br_id*."""
    overrides = await load_overrides(session, br_id)
    comp = await fleet_composition(
        session,
        br_id,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
        overrides=overrides,
        settings=settings,
        char_to_user=char_to_user,
    )
    friendly: list[CompositionPilot] = []
    for side in comp.sides:
        if side.side_kind == "friendly":
            friendly.extend(side.pilots)
    m = await compute_broadcast_metrics(session, br_id)
    return assemble_performance(friendly, m, viewer_character_ids, elevated)
