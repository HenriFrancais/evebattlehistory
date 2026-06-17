"""Pydantic response schemas for BR API endpoints."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class BrCreate(BaseModel):
    url: str
    title: str | None = None


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
