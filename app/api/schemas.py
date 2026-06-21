"""Pydantic response schemas for BR API endpoints."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class BrSourceIn(BaseModel):
    """One source entry for POST /api/brs or POST /api/brs/{id}/sources."""

    kind: str  # "link" | "window"
    # For kind=link
    url: str | None = None
    # For kind=window
    system_id: int | None = None
    window_start: dt.datetime | None = None
    window_end: dt.datetime | None = None
    label: str | None = None


class BrCreate(BaseModel):
    # Back-compat: accepts {url, title?} OR {sources:[...], title?}
    url: str | None = None
    title: str | None = None
    sources: list[BrSourceIn] | None = None


class BrSourceOut(BaseModel):
    """One source row returned by GET /api/brs/{id}/sources."""

    source_id: int
    br_id: str
    kind: str
    url: str | None
    system_id: int | None
    window_start: dt.datetime | None
    window_end: dt.datetime | None
    label: str | None
    status: str
    error_text: str | None
    km_count: int


class BrPatch(BaseModel):
    title: str


class BrCreated(BaseModel):
    br_id: str
    status: str


class BrStatus(BaseModel):
    br_id: str
    status: str
    progress_pct: int
    error_text: str | None


class FightSideOut(BaseModel):
    side_idx: int
    side_kind: str | None  # 'friendly' | 'hostile' | 'unassigned'
    pilot_count: int
    isk_lost: float
    losses: int = 0  # ships destroyed on this side


class FightOut(BaseModel):
    fight_id: int
    system_id: int
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    isk_destroyed_total: float
    largest_side_pilots: int
    capitals_involved: bool = False
    sides: list[FightSideOut]


class BrSummary(BaseModel):
    br_id: str
    title: str | None
    source: str
    source_url: str | None
    status: str
    progress_pct: int
    result: str | None
    isk_efficiency: float | None
    our_isk_destroyed: float
    our_isk_lost: float
    fight_count: int
    battle_at: dt.datetime | None
    created_at: dt.datetime
    # Timeline-list extras (populated by list/filter endpoints; default elsewhere).
    systems: list[str] = []
    our_name: str | None = None
    opponent_name: str | None = None
    friendly_pilots: int = 0
    enemy_pilots: int = 0
    you_present: bool = False
    your_present: int = 0
    your_logged: int = 0
    roster_present: int = 0
    roster_logged: int = 0


class BrDetail(BrSummary):
    fights: list[FightOut]
    systems: list[str] = []


class BrListSummary(BaseModel):
    total: int
    wins: int
    ties: int
    losses: int
    win_rate: float
    total_isk_destroyed: float
    total_isk_lost: float


class BrListResponse(BaseModel):
    summary: BrListSummary
    brs: list[BrSummary]


# ---------------------------------------------------------------------------
# Timeline schemas (Task 3.1)
# ---------------------------------------------------------------------------


class TimelineFightInfo(BaseModel):
    """Fight metadata for drawing fight-boundary markers on the timeline."""

    fight_id: int
    seq: int
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    system_id: int


class TimelineSeriesOut(BaseModel):
    """One (effect_type, direction) series aligned to the shared x axis.

    ``effect_type`` and ``direction`` are ``null`` when the source LogEventBucket
    stored ``""`` (the NULL → "" convention; see LogEventBucket docstring).
    The ``key`` field uses ``"unknown"`` in place of ``""`` so it is safe to use
    as a display label.
    """

    key: str
    effect_type: str | None
    direction: str | None
    values: list[float | None]
    event_count: int


class CharacterTimelineOut(BaseModel):
    """uPlot-aligned timeline for one character within one battle report."""

    x: list[int]
    """Sorted, unique epoch-second timestamps of every bucket across all series."""
    series: list[TimelineSeriesOut]
    fights: list[TimelineFightInfo]
    t_start: int | None
    t_end: int | None


class TimelineEventOut(BaseModel):
    """One raw LogEvent row for drill-down display."""

    ts: dt.datetime
    direction: str | None
    effect_type: str | None
    amount: float | None
    quality: str | None
    other_name: str | None
    other_ship_name: str | None
    module_name: str | None


class TimelineEventListOut(BaseModel):
    """Capped list of raw events with a truncation flag."""

    events: list[TimelineEventOut]
    truncated: bool


# ---------------------------------------------------------------------------
# Reconcile schemas (Task 4.1)
# ---------------------------------------------------------------------------


class CharacterReconcileRowOut(BaseModel):
    """Per-character damage reconciliation row.

    ``delta = log_damage_out - km_damage_attributed``.
    A positive delta surfaces damage applied to ships that didn't die.
    """

    character_id: int
    character_name: str | None = None
    log_damage_out: float
    log_damage_in: float
    km_damage_attributed: float
    delta: float


class DpsPointOut(BaseModel):
    """One time-bucket in the outgoing DPS series."""

    bucket_ts_epoch: int
    sum_damage_out: float


class FightReconcileOut(BaseModel):
    """Damage reconciliation result for a single fight."""

    rows: list[CharacterReconcileRowOut]
    dps_series: list[DpsPointOut]


# ---------------------------------------------------------------------------
# EWAR schemas (Task 4.1)
# ---------------------------------------------------------------------------


class EwarRowOut(BaseModel):
    """One summary row for tackle/EWAR effects.

    For scram/disrupt rows: source_name/target_name identify the real tackler and
    target from the deduped set.  character_id is 0 (not meaningful).
    For jam rows: character_id identifies the log owner; source_name/target_name are None.
    """

    character_id: int
    effect_type: str
    direction: str
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime
    source_name: str | None = None
    target_name: str | None = None


class CapRowOut(BaseModel):
    """One (character, effect_type, direction) summary for cap-warfare effects."""

    character_id: int
    effect_type: str
    direction: str
    sum_amount: float
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


class LogiRowOut(BaseModel):
    """One (character, effect_type, direction) summary for remote repair effects."""

    character_id: int
    effect_type: str
    direction: str
    sum_amount: float
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


class FightEwarOut(BaseModel):
    """EWAR + logi effectiveness result for a single fight."""

    ewar: list[EwarRowOut]
    cap: list[CapRowOut]
    logi: list[LogiRowOut]


# ---------------------------------------------------------------------------
# Filter schemas (Task 4.2)
# ---------------------------------------------------------------------------


class FightWithBrId(FightOut):
    br_id: str


class FightFilterRequest(BaseModel):
    tree: dict  # type: ignore[type-arg]
    br_id: str | None = None


class BrFilterRequest(BaseModel):
    tree: dict  # type: ignore[type-arg]


class FilteredBrResponse(BaseModel):
    summary: BrListSummary
    brs: list[BrSummary]


# ---------------------------------------------------------------------------
# Fleet timeline schemas (E3)
# ---------------------------------------------------------------------------


class FleetSeriesOut(BaseModel):
    """One (effect_type, direction) series aligned to the shared fleet x axis."""

    key: str
    """Stable identity '{effect_type}:{direction}' (used for toggles)."""
    effect_type: str
    direction: str
    metric: str
    """'amount' (HP/GJ from sum_amount) or 'count' (EWAR applications)."""
    values: list[float | None]


class SideEntityOut(BaseModel):
    """One assignable entity (alliance, or corp without an alliance) in a BR."""

    entity_type: str  # 'alliance' | 'corp'
    entity_id: int
    name: str
    side: str  # 'friendly' | 'hostile'
    overridden: bool  # True if an FC/HC override is in effect
    baseline: bool  # True if a permanent friendly blue


class BrSidesOut(BaseModel):
    """Entities in a BR with their current side + whether the caller may edit."""

    entities: list[SideEntityOut]
    can_edit: bool


class SideOverrideIn(BaseModel):
    """Set ('friendly'/'hostile') or clear (None) a per-BR side override."""

    entity_type: str
    entity_id: int
    side: str | None


class KillEventOut(BaseModel):
    """One kill event overlaid on the fleet timeline."""

    ts: int
    killmail_id: int
    victim_character_id: int | None
    victim_character_name: str | None
    victim_ship_name: str
    victim_ship_type_id: int | None
    side_kind: str | None
    isk: float | None


class ContributionOut(BaseModel):
    """One source→target aggregate within a clicked time bucket."""

    source_character_id: int | None
    source_name: str
    target_name: str
    effect_type: str
    direction: str
    group: str  # 'damage' | 'cap' | 'ewar'
    value: float
    module_name: str | None = None
    icon_type_id: int | None = None
    weapon_category: str | None = None
    target_ship: str | None = None
    quality: str | None = None


class ContributionsOut(BaseModel):
    from_ts: int
    to_ts: int
    rows: list[ContributionOut]


class LeaderEntryOut(BaseModel):
    """Top character for one metric in a single time bucket."""

    name: str
    ship: str | None
    amount: float


class LeadersOut(BaseModel):
    """Per-bucket leaders split by the target's side (3 fields)."""

    top_friendly_dmg_taken: LeaderEntryOut | None
    top_hostile_dmg_taken: LeaderEntryOut | None
    top_friendly_rep_recv: LeaderEntryOut | None


class FleetTimelineOut(BaseModel):
    """Aggregated fleet-level timeline for one battle report."""

    x: list[int]
    series: list[FleetSeriesOut]
    kills: list[KillEventOut]
    fights: list[TimelineFightInfo]
    bucket_seconds: int
    t_start: int | None
    t_end: int | None
    leaders: list[LeadersOut]


# ---------------------------------------------------------------------------
# Fleet composition schemas
# ---------------------------------------------------------------------------


class CompositionShipOut(BaseModel):
    ship_type_id: int
    ship_name: str
    count: int


class WeaponEffectOut(BaseModel):
    type_id: int
    name: str
    role: str


class CompositionPilotOut(BaseModel):
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    reship: bool = False
    killmail_id: int | None = None
    user_name: str | None = None
    weapons: list[WeaponEffectOut] = []


class CompositionSideOut(BaseModel):
    side_kind: str  # 'friendly' | 'hostile' | 'unassigned'
    pilot_count: int
    ships: list[CompositionShipOut]
    pilots: list[CompositionPilotOut]


class CompositionOut(BaseModel):
    by_user_available: bool
    sides: list[CompositionSideOut]


# ---------------------------------------------------------------------------
# Damage attribution schemas (Task 15)
# ---------------------------------------------------------------------------


class AttackerDamageRowOut(BaseModel):
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    final_blow: bool


class LossDamageAttributionOut(BaseModel):
    killmail_id: int
    damage_taken: int | None
    total_attributed: int
    attackers: list[AttackerDamageRowOut]


# ---------------------------------------------------------------------------
# Damage leaderboard schemas (Task 16)
# ---------------------------------------------------------------------------


class LeaderboardRowOut(BaseModel):
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    log_damage_out: float | None  # None unless logs present (Task 21 wires overlay)


class BrDamageLeaderboardOut(BaseModel):
    rows: list[LeaderboardRowOut]  # sorted by damage_done desc
    total_attributed: int
    logs_present: bool


# ---------------------------------------------------------------------------
# Item loss breakdown schemas (Task 19)
# ---------------------------------------------------------------------------


class ItemLossRowOut(BaseModel):
    type_id: int
    name: str
    location: str
    qty_destroyed: int
    qty_dropped: int


class SlotLossOut(BaseModel):
    location: str
    destroyed_qty: int
    dropped_qty: int
    value: float | None  # always None — no per-item price source
    items: list[ItemLossRowOut]


class ItemLossBreakdownOut(BaseModel):
    killmail_id: int
    slots: list[SlotLossOut]  # ordered: high,med,low,rig,subsystem,drone_bay,cargo,implant,other
