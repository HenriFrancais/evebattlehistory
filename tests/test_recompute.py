"""Tests for the bulk BR-outcome recompute (app.analytics.recompute)."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db.models import (
    Alliance,
    BattleReport,
    BrFight,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    SolarSystem,
)

OUR_ALLI = 99006113  # baseline NV blue ("us")
ENEMY = 88880001


_seed_counter = 0


async def _seed_br(  # type: ignore[no-untyped-def]
    session, *, friendly_loss: float, hostile_loss: float, stored_result: str | None
) -> str:
    """Seed a one-fight BR: one friendly victim and one hostile victim, with the
    given ISK total_values, and a deliberately-stored ``result``."""
    global _seed_counter
    _seed_counter += 1
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    sid = 31070000 + _seed_counter
    session.add(SolarSystem(system_id=sid, name=f"J-{sid}", security=None))
    await session.flush()
    fight = Fight(
        system_id=sid, started_at=now, ended_at=now, isk_destroyed_total=1.0,
        largest_side_pilots=1, capitals_involved=False, distinct_alliance_count=2,
    )
    session.add(fight)
    await session.flush()
    base_km = sid * 10
    # Friendly victim (our loss) and hostile victim (what we destroyed).
    session.add(Killmail(killmail_id=base_km + 1, killmail_time=now, solar_system_id=sid,
                         victim_character_id=10, victim_corporation_id=None,
                         victim_alliance_id=OUR_ALLI, victim_ship_type_id=1,
                         total_value=friendly_loss))
    session.add(Killmail(killmail_id=base_km + 2, killmail_time=now, solar_system_id=sid,
                         victim_character_id=20, victim_corporation_id=None,
                         victim_alliance_id=ENEMY, victim_ship_type_id=1,
                         total_value=hostile_loss))
    await session.flush()
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=base_km + 1, side_idx=0))
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=base_km + 2, side_idx=1))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="t", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             result=stored_result, created_at=now))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.commit()
    return br_id


@pytest.mark.asyncio
async def test_recompute_reclassifies_under_new_band(db_session_maker):
    """A 0.55-efficiency BR stored as 'tie' (old 40-60 band) becomes 'win' under
    the new 48-52 band; an already-correct BR is left unchanged."""
    from app.analytics.recompute import recompute_all_brs
    from app.db.models import Character

    async with db_session_maker() as session:
        session.add(Alliance(alliance_id=OUR_ALLI, name="No Vacancies.",
                             last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        session.add(Alliance(alliance_id=ENEMY, name="Enemy",
                             last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        session.add(InventoryType(type_id=1, name="TestShip"))
        for cid in (10, 20):
            session.add(Character(character_id=cid, name=f"Pilot {cid}",
                                  last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        await session.commit()
        # eff = 55 / (45 + 55) = 0.55 → old band: tie; new band: win.
        changed_br = await _seed_br(session, friendly_loss=45.0, hostile_loss=55.0,
                                    stored_result="tie")
        # eff = 90 / (10 + 90) = 0.90 → win under both bands; already correct.
        stable_br = await _seed_br(session, friendly_loss=10.0, hostile_loss=90.0,
                                   stored_result="win")

    async with db_session_maker() as session:
        total, changed = await recompute_all_brs(
            session, baseline_alliances={OUR_ALLI}, baseline_corps=set(),
        )
        await session.commit()

    assert total == 2
    assert changed == 1

    async with db_session_maker() as session:
        from sqlalchemy import select

        results = dict(
            (
                await session.execute(select(BattleReport.br_id, BattleReport.result))
            ).all()
        )
    assert results[changed_br] == "win"
    assert results[stable_br] == "win"


@pytest.mark.asyncio
async def test_recompute_is_idempotent(db_session_maker):
    """A second pass reports zero changes."""
    from app.analytics.recompute import recompute_all_brs
    from app.db.models import Character

    async with db_session_maker() as session:
        session.add(Alliance(alliance_id=OUR_ALLI, name="No Vacancies.",
                             last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        session.add(Alliance(alliance_id=ENEMY, name="Enemy",
                             last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        session.add(InventoryType(type_id=1, name="TestShip"))
        for cid in (10, 20):
            session.add(Character(character_id=cid, name=f"Pilot {cid}",
                                  last_seen_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC)))
        await session.commit()
        await _seed_br(session, friendly_loss=45.0, hostile_loss=55.0, stored_result="tie")

    async with db_session_maker() as session:
        await recompute_all_brs(session, baseline_alliances={OUR_ALLI}, baseline_corps=set())
        await session.commit()

    async with db_session_maker() as session:
        _total, changed = await recompute_all_brs(
            session, baseline_alliances={OUR_ALLI}, baseline_corps=set(),
        )
        await session.commit()

    assert changed == 0
