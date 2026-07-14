"""Tests for the pure fleet-broadcast parser + date reconstruction."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.logs.broadcast_parse import (
    choose_anchor_date,
    parse_broadcast,
    reconstruct_dates,
)

FIXTURE = Path(__file__).parent / "fixtures" / "broadcasts" / "sample_broadcast.txt"


def _parse():
    return parse_broadcast(FIXTURE.read_text())


def test_keeps_only_broadcast_lines_in_reverse_order():
    p = _parse()
    kinds = [b.kind for b in p.broadcasts]
    # target, needs_shield, needs_capacitor, target, needs_armor, repair, target
    assert kinds == [
        "target",
        "needs_shield",
        "needs_capacitor",
        "target",
        "needs_armor",
        "repair",
        "target",
    ]
    # seq preserves original (reverse-chronological) order.
    assert [b.seq for b in p.broadcasts] == list(range(len(p.broadcasts)))


def test_noise_is_dropped():
    p = _parse()
    raws = " ".join(b.raw for b in p.broadcasts)
    for noise in ("left fleet", "has looted", "fleet boss", "is in position", "joined as"):
        assert noise not in raws
    assert p.stats["matched"] == 7
    assert p.stats["noise"] == 5


def test_subject_and_ship_extracted():
    p = _parse()
    first = p.broadcasts[0]
    assert first.kind == "target"
    assert first.subject_name == "Enemy One"
    assert first.subject_ship == "Rattlesnake"
    repair = next(b for b in p.broadcasts if b.kind == "repair")
    assert repair.subject_name == "Dave Pilot"


def test_capacitor_and_armor_variants():
    p = _parse()
    kinds = {b.subject_name: b.kind for b in p.broadcasts}
    assert kinds["Bob Pilot"] == "needs_capacitor"
    assert kinds["Carol Pilot"] == "needs_armor"
    assert kinds["Alice Pilot"] == "needs_shield"


def test_date_reconstruction_crosses_midnight():
    p = _parse()
    # Newest line is 00:02:10 on the day after the fight; walk backwards over midnight.
    anchor = dt.date(2026, 7, 12)
    ts = reconstruct_dates(p.broadcasts, anchor)
    assert ts[0] == dt.datetime(2026, 7, 12, 0, 2, 10)
    # The 23:59:50 line must land on the *previous* day.
    carol_idx = next(i for i, b in enumerate(p.broadcasts) if b.subject_name == "Carol Pilot")
    assert ts[carol_idx] == dt.datetime(2026, 7, 11, 23, 59, 50)
    # Strictly non-increasing in absolute time.
    assert all(ts[i] <= ts[i - 1] for i in range(1, len(ts)))


def test_choose_anchor_date_picks_nearest_day():
    p = _parse()
    newest = p.broadcasts[0].tod  # 00:02:10
    # Fight ended 2026-07-12 00:05 UTC → newest 00:02 belongs to 07-12.
    assert choose_anchor_date(newest, dt.datetime(2026, 7, 12, 0, 5)) == dt.date(2026, 7, 12)
    # Fight ended 2026-07-11 23:58 → the 00:02 line is best placed on 07-12 (4 min later)
    # rather than 07-11 (nearly 24h earlier).
    assert choose_anchor_date(newest, dt.datetime(2026, 7, 11, 23, 58)) == dt.date(2026, 7, 12)


def test_empty_and_reconstruct_empty():
    p = parse_broadcast("garbage\nno timestamps here\n")
    assert p.broadcasts == []
    assert reconstruct_dates([], dt.date(2026, 7, 12)) == []
