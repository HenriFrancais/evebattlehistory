"""Tests for per-BR timeline enrichment (opponent, pilots, log coverage)."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db.models import (
    Alliance,
    BattleReport,
    BrFight,
    Character,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
    SolarSystem,
)

OUR_ALLI = 99006113  # baseline NV blue
ENEMY_BIG = 88880001
ENEMY_SMALL = 88880002


async def _seed_br(session) -> str:  # type: ignore[no-untyped-def]
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    session.add(SolarSystem(system_id=31077777, name="J-Tl", security=None))
    session.add(Alliance(alliance_id=OUR_ALLI, name="No Vacancies.", last_seen_at=now))
    session.add(Alliance(alliance_id=ENEMY_BIG, name="Big Enemy", last_seen_at=now))
    session.add(Alliance(alliance_id=ENEMY_SMALL, name="Small Enemy", last_seen_at=now))
    session.add(InventoryType(type_id=1, name="TestShip"))
    for cid in (10, 11, 20, 21, 30):
        session.add(Character(character_id=cid, name=f"Pilot {cid}", last_seen_at=now))
    await session.flush()
    fight = Fight(system_id=31077777, started_at=now, ended_at=now,
                  isk_destroyed_total=1.0, largest_side_pilots=3,
                  capitals_involved=False, distinct_alliance_count=3)
    session.add(fight)
    await session.flush()
    # One killmail; pilots spread across alliances via attackers.
    session.add(Killmail(killmail_id=1, killmail_time=now, solar_system_id=31077777,
                         victim_character_id=10, victim_corporation_id=None,
                         victim_alliance_id=OUR_ALLI, victim_ship_type_id=1, total_value=1.0))
    await session.flush()
    # Big Enemy fields 2 pilots, Small Enemy 1, us (attacker) 1.
    attackers = [
        (0, 20, ENEMY_BIG), (1, 21, ENEMY_BIG), (2, 30, ENEMY_SMALL), (3, 11, OUR_ALLI),
    ]
    for idx, char, alli in attackers:
        session.add(KillmailAttacker(killmail_id=1, attacker_idx=idx, character_id=char,
                                     corporation_id=None, alliance_id=alli, ship_type_id=1,
                                     damage_done=1, final_blow=(idx == 0)))
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=1, side_idx=0))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="t", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=now))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.commit()
    return br_id


@pytest.mark.asyncio
async def test_enrich_picks_largest_opponent_and_counts_pilots(db_session_maker):
    from app.config import get_settings
    from app.fights.timeline_rows import enrich_br_rows

    async with db_session_maker() as session:
        br_id = await _seed_br(session)

    async with db_session_maker() as session:
        extras = await enrich_br_rows(
            session, get_settings(), [br_id],
            user_name="nobody",
            baseline_alliances={OUR_ALLI}, baseline_corps=set(),
        )

    row = extras[br_id]
    # Friendly = our victim (char 10) + our attacker (char 11) = 2 pilots.
    assert row.friendly_pilots == 2
    assert row.our_name == "No Vacancies."
    # Enemy pilots: 2 (Big) + 1 (Small) = 3; opponent is the largest = Big Enemy.
    assert row.enemy_pilots == 3
    assert row.opponent_name == "Big Enemy"


async def _seed_second_br(session) -> str:  # type: ignore[no-untyped-def]
    """A second BR reusing the seeded entities but a different shape: a lone
    Small-Enemy victim with one friendly attacker."""
    now = dt.datetime(2026, 6, 2, tzinfo=dt.UTC)
    session.add(SolarSystem(system_id=31077778, name="J-Tl2", security=None))
    await session.flush()
    fight = Fight(system_id=31077778, started_at=now, ended_at=now,
                  isk_destroyed_total=1.0, largest_side_pilots=1,
                  capitals_involved=False, distinct_alliance_count=2)
    session.add(fight)
    await session.flush()
    session.add(Killmail(killmail_id=2, killmail_time=now, solar_system_id=31077778,
                         victim_character_id=30, victim_corporation_id=None,
                         victim_alliance_id=ENEMY_SMALL, victim_ship_type_id=1, total_value=1.0))
    await session.flush()
    session.add(KillmailAttacker(killmail_id=2, attacker_idx=0, character_id=11,
                                 corporation_id=None, alliance_id=OUR_ALLI, ship_type_id=1,
                                 damage_done=1, final_blow=True))
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=2, side_idx=0))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="t", source_url="x", source_ref="r2",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=now))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.commit()
    return br_id


@pytest.mark.asyncio
async def test_enrich_batches_multiple_brs_independently(db_session_maker):
    """Batched enrichment must keep per-BR results isolated (no cross-contamination)."""
    from app.config import get_settings
    from app.fights.timeline_rows import enrich_br_rows

    async with db_session_maker() as session:
        br1 = await _seed_br(session)
    async with db_session_maker() as session:
        br2 = await _seed_second_br(session)

    async with db_session_maker() as session:
        extras = await enrich_br_rows(
            session, get_settings(), [br1, br2],
            user_name="nobody",
            baseline_alliances={OUR_ALLI}, baseline_corps=set(),
        )

    # BR1 unchanged from the single-BR case.
    assert extras[br1].friendly_pilots == 2
    assert extras[br1].enemy_pilots == 3
    assert extras[br1].opponent_name == "Big Enemy"
    assert extras[br1].systems == ["J-Tl"]
    assert extras[br1].system_ids == [31077777]
    # BR2 has its own shape: 1 friendly (char 11), 1 enemy (Small Enemy char 30).
    assert extras[br2].friendly_pilots == 1
    assert extras[br2].enemy_pilots == 1
    assert extras[br2].our_name == "No Vacancies."
    assert extras[br2].opponent_name == "Small Enemy"
    assert extras[br2].systems == ["J-Tl2"]
    assert extras[br2].system_ids == [31077778]
