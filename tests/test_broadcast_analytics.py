"""Metric-computation tests for app.analytics.broadcasts.

Seeds Broadcast + LogEvent + Killmail rows directly and asserts each metric path,
mirroring the owner-relative (damage/rep) vs source/target (tackle) attribution.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import insert

from app.analytics.broadcasts import compute_broadcast_metrics
from app.db.models import (
    BattleReport,
    Broadcast,
    BroadcastFile,
    BrFight,
    Character,
    Fight,
    FightKill,
    GamelogFile,
    InventoryType,
    Killmail,
    LogEvent,
    SolarSystem,
)

BR_ID = "BR"
D = dt.datetime(2026, 7, 11, 23, 0, 0)


def _t(sec: int) -> dt.datetime:
    return D + dt.timedelta(seconds=sec)


async def _seed(s) -> None:
    s.add(SolarSystem(system_id=1, name="J1"))
    s.add(BattleReport(br_id=BR_ID, source="x", source_url="", source_ref="",
                       created_by_user="u", created_at=D, battle_at=D))
    s.add(Fight(fight_id=1, system_id=1,
                started_at=D - dt.timedelta(minutes=5), ended_at=D + dt.timedelta(minutes=30)))
    s.add(BrFight(br_id=BR_ID, fight_id=1, seq=0))
    s.add(InventoryType(type_id=100, name="Nighthawk"))
    s.add(InventoryType(type_id=670, name="Capsule"))
    for cid, name in [(10, "Alice"), (11, "Bob"), (12, "Carol"), (99, "Shooter")]:
        s.add(Character(character_id=cid, name=name, last_seen_at=D))
    s.add(GamelogFile(file_id=1, uploaded_by_user="u", resolved_via="x", stored_path="/g",
                      sha256="g1", mime="text/plain", size=1, parse_status="parsed",
                      event_count=0, uploaded_at=D))
    bf = BroadcastFile(broadcast_file_id=1, br_id=BR_ID, uploaded_by_user="u", stored_path="/x",
                       sha256="s1", mime="text/plain", size=1, parse_status="parsed",
                       broadcast_count=0, superseded=False, uploaded_at=D)
    s.add(bf)
    await s.flush()


def _bc(**kw):
    base = dict(file_id=1, br_id=BR_ID, fight_id=1, subject_ship="Nighthawk",
               subject_character_id=None, raw_line="r")
    base.update(kw)
    return base


def _ev(**kw):
    base = dict(file_id=1, fight_id=1, dedupe_suppressed=False, authoritative=False)
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_all_metrics(db_session_maker):
    async with db_session_maker() as s:
        await _seed(s)
        broadcasts = [
            # Target call on enemy at +100; already primaried (+90), Bob fires +106 (complied).
            _bc(ts=_t(100), kind="target", subject_name="Enemy One", subject_ship="Rattlesnake", seq=0),
            # Target call never answered.
            _bc(ts=_t(200), kind="target", subject_name="Ghost", subject_ship="Sabre", seq=1),
            # Alice needs shield +300: repped +305 (logi 5s), justified (dmg +298), reaction (onset +290 -> 10s).
            _bc(ts=_t(300), kind="needs_shield", subject_name="Alice", subject_character_id=10, seq=2),
            # Alice false broadcast +600 (no incoming damage nearby).
            _bc(ts=_t(600), kind="needs_shield", subject_name="Alice", subject_character_id=10, seq=3),
            # Carol needs shield +500, dies +503 -> late_broadcast.
            _bc(ts=_t(500), kind="needs_shield", subject_name="Carol", subject_character_id=12, seq=4),
        ]
        await s.execute(insert(Broadcast), broadcasts)
        events = [
            _ev(character_id=99, ts=_t(90), direction="out", effect_type="damage", other_name="Enemy One"),
            _ev(character_id=11, ts=_t(106), direction="out", effect_type="damage", other_name="Enemy One"),
            _ev(character_id=10, ts=_t(290), direction="in", effect_type="damage", other_name="Enemy One"),
            _ev(character_id=10, ts=_t(298), direction="in", effect_type="damage", other_name="Enemy One"),
            _ev(character_id=10, ts=_t(305), direction="in", effect_type="rep_shield", amount=500.0),
            _ev(character_id=12, ts=_t(480), direction="in", effect_type="damage", other_name="Enemy One"),
        ]
        await s.execute(insert(LogEvent), events)
        # Carol dies in a Nighthawk (ship loss, not a pod) at +503.
        s.add(Killmail(killmail_id=5001, killmail_time=_t(503), solar_system_id=1,
                       victim_character_id=12, victim_ship_type_id=100))
        s.add(FightKill(fight_id=1, killmail_id=5001, side_idx=0))
        await s.commit()

        m = await compute_broadcast_metrics(s, BR_ID)

    assert m.has_broadcasts
    # Target timing
    enemy = next(r for r in m.targets.rows if r.subject_name == "Enemy One")
    assert enemy.first_fire_delta_s == 6.0
    assert enemy.already_primaried is True
    assert enemy.complied is True
    ghost = next(r for r in m.targets.rows if r.subject_name == "Ghost")
    assert ghost.first_fire_delta_s is None
    assert m.targets.unanswered_count == 1
    # Per-pilot switch: Shooter fired BEFORE the call (excluded); only Bob counts.
    pilots = {p.character_name: p for p in m.targets.per_pilot}
    assert set(pilots) == {"Bob"}
    assert pilots["Bob"].median_switch_s == 6.0
    # Rep metrics: logi response for Alice's +300 broadcast (repped at +305).
    a300 = next(r for r in m.reps.rows if r.subject_character_id == 10 and r.logi_response_s == 5.0)
    assert a300.justified is True
    assert a300.reaction_s == 10.0
    assert m.reps.median_logi_response_s == 5.0
    # two false broadcasts (Alice@600 no dmg, Carol@500 dmg 20s away outside ±10 window)
    assert m.reps.false_broadcast_rate == round(2 / 3, 3)
    # Death classification
    carol = next(d for d in m.deaths if d.character_name == "Carol")
    assert carol.classification == "late_broadcast"
    assert carol.last_broadcast_delta_s == 3.0
    # Summary
    assert m.summary.median_time_to_fire_s == 6.0
    assert m.summary.compliance_rate == 0.5
    assert m.summary.deaths_flagged == 1


@pytest.mark.asyncio
async def test_empty_when_no_broadcasts(db_session_maker):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        m = await compute_broadcast_metrics(s, BR_ID)
    assert m.has_broadcasts is False
    assert m.targets.rows == []
    assert m.deaths == []


@pytest.mark.asyncio
async def test_outliers_beyond_match_window_are_ignored(db_session_maker):
    # A fire 300s after the call and a rep 90s after the request are coincidental,
    # not real responses — they must count as unanswered, not huge deltas.
    async with db_session_maker() as s:
        await _seed(s)
        await s.execute(insert(Broadcast), [
            _bc(ts=_t(100), kind="target", subject_name="Slowpoke", subject_ship="Rattlesnake", seq=0),
            _bc(ts=_t(200), kind="needs_shield", subject_name="Alice", subject_character_id=10, seq=1),
        ])
        await s.execute(insert(LogEvent), [
            # fleet only fires on Slowpoke 300s later (way past the 20s window)
            _ev(character_id=11, ts=_t(400), direction="out", effect_type="damage", other_name="Slowpoke"),
            # Alice is only repped 90s after her call
            _ev(character_id=10, ts=_t(290), direction="in", effect_type="rep_shield", amount=500.0),
        ])
        await s.commit()
        m = await compute_broadcast_metrics(s, BR_ID)
    target = m.targets.rows[0]
    assert target.first_fire_delta_s is None       # 300s → not a response
    assert target.complied is False
    assert m.targets.unanswered_count == 1
    assert m.targets.per_pilot == []               # no within-window switch to score
    rep = m.reps.rows[0]
    assert rep.logi_response_s is None             # 90s → not repaired in time
    assert m.reps.median_logi_response_s is None


@pytest.mark.asyncio
async def test_broadcast_lead_time_proactive_vs_late(db_session_maker):
    # Proactive: Alice calls, then a 6000 HP/3s burst lands 3s later -> positive lead.
    # Late: Bob's burst is already landing before he calls -> lead <= 0, flagged late.
    async with db_session_maker() as s:
        await _seed(s)
        await s.execute(insert(Broadcast), [
            _bc(ts=_t(300), kind="needs_shield", subject_name="Alice", subject_character_id=10, seq=0),
            _bc(ts=_t(500), kind="needs_shield", subject_name="Bob", subject_character_id=11, seq=1),
        ])
        ev = []
        # Alice: burst starts at +303 (3 hits x 2100 within 3s = 6300 >= 5000).
        for dt_off in (303, 304, 305):
            ev.append(_ev(character_id=10, ts=_t(dt_off), direction="in", effect_type="damage",
                          other_name="Foe", amount=2100.0))
        # Bob: burst already underway at +496..+498 (before his +500 call).
        for dt_off in (496, 497, 498):
            ev.append(_ev(character_id=11, ts=_t(dt_off), direction="in", effect_type="damage",
                          other_name="Foe", amount=2100.0))
        await s.execute(insert(LogEvent), ev)
        await s.commit()
        m = await compute_broadcast_metrics(s, BR_ID)
    rows = {r.subject_character_id: r for r in m.reps.rows}
    # T-minus convention: negative = called before the burst (early), positive = late.
    assert rows[10].damage_lead_s == -3.0     # warned 3s before the burst
    assert rows[10].broadcast_late is False
    assert rows[11].damage_lead_s == 4.0      # burst already landing 4s before the call
    assert rows[11].broadcast_late is True
    assert m.quality.late_broadcasts == 1
    assert m.summary.late_broadcast_rate == 0.5


@pytest.mark.asyncio
async def test_tackle_counts_as_fire_on_target(db_session_maker):
    # A scram on the called enemy (source/target attribution) counts as fleet fire.
    async with db_session_maker() as s:
        await _seed(s)
        await s.execute(insert(Broadcast), [
            _bc(ts=_t(100), kind="target", subject_name="Tackled Foe", subject_ship="Loki", seq=0),
        ])
        await s.execute(insert(LogEvent), [
            _ev(character_id=11, ts=_t(108), direction="out", effect_type="scram",
                source_name="Bob", target_name="Tackled Foe"),
        ])
        await s.commit()
        m = await compute_broadcast_metrics(s, BR_ID)
    row = m.targets.rows[0]
    assert row.first_fire_delta_s == 8.0
    assert row.complied is True
