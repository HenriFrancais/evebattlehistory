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
