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
    side_kind: str | None
    pilot_count: int
    isk_lost: float


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


class BrDetail(BrSummary):
    fights: list[FightOut]


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
    """One (character, effect_type, direction) summary for tackle/EWAR effects."""

    character_id: int
    effect_type: str
    direction: str
    event_count: int
    first_ts: dt.datetime
    last_ts: dt.datetime


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
    """One named series aligned to the shared fleet x axis."""

    key: str
    """Fixed key: one of 'dps_out', 'remote_rep', 'ewar', 'cap_warfare'."""
    values: list[float | None]


class KillEventOut(BaseModel):
    """One kill event overlaid on the fleet timeline."""

    ts: int
    killmail_id: int
    victim_character_id: int | None
    victim_ship_name: str
    side_kind: str | None
    isk: float | None


class FleetTimelineOut(BaseModel):
    """Aggregated fleet-level timeline for one battle report."""

    x: list[int]
    series: list[FleetSeriesOut]
    kills: list[KillEventOut]
    fights: list[TimelineFightInfo]
    bucket_seconds: int
    t_start: int | None
    t_end: int | None
