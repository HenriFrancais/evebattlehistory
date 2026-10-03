"""One side resolver for every reader: baseline blues, FC/HC entity overrides and
per-character overrides, in that precedence. The BR headline counts as "destroyed
by us" only kills a friendly pilot was on — a third party dying to someone else is
not our kill."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    Alliance,
    BattleReport,
    BrCharSide,
    BrFight,
    Character,
    Fight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
    SolarSystem,
)
from tests.conftest import CREATOR_HEADERS

NV = 99006113  # baseline friendly alliance
HOSTILE = 99000010
THIRD_A = 99000020
THIRD_B = 99000030
NV_PILOT, HOSTILE_PILOT, THIRD_PILOT_A, THIRD_PILOT_B, LONER = 1001, 2001, 3001, 4001, 5001
T = dt.datetime(2026, 6, 1, 0, 1, tzinfo=dt.UTC)


async def _seed(session) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    """Three-way brawl:
      km1  HOSTILE pilot dies (10 ISK), NV pilot on the mail      → our kill
      km2  THIRD_A pilot dies (100 ISK), only THIRD_B on the mail → NOT our kill
      km3  NV pilot dies (5 ISK) to HOSTILE                        → our loss
      km4  LONER (no alliance, NPC corp) dies (7 ISK) to HOSTILE   → unassigned
    """
    now = dt.datetime.now(dt.UTC)
    sid = 31009998
    session.add(SolarSystem(system_id=sid, name="J-RES", security=None))
    for aid in (NV, HOSTILE, THIRD_A, THIRD_B):
        session.add(Alliance(alliance_id=aid, name=f"A{aid}", last_seen_at=now))
    session.add(InventoryType(type_id=1, name="TestShip"))
    await session.flush()
    for cid, alli in ((NV_PILOT, NV), (HOSTILE_PILOT, HOSTILE), (THIRD_PILOT_A, THIRD_A),
                      (THIRD_PILOT_B, THIRD_B), (LONER, None)):
        session.add(Character(character_id=cid, name=f"P{cid}", alliance_id=alli,
                              last_seen_at=now))
    fight = Fight(system_id=sid, started_at=T, ended_at=T + dt.timedelta(minutes=5),
                  isk_destroyed_total=0.0, largest_side_pilots=1,
                  capitals_involved=False, distinct_alliance_count=4)
    session.add(fight)
    await session.flush()
    kills = [
        (1, HOSTILE_PILOT, HOSTILE, 10.0, NV_PILOT, NV),
        (2, THIRD_PILOT_A, THIRD_A, 100.0, THIRD_PILOT_B, THIRD_B),
        (3, NV_PILOT, NV, 5.0, HOSTILE_PILOT, HOSTILE),
        (4, LONER, None, 7.0, HOSTILE_PILOT, HOSTILE),
    ]
    for km_id, victim, v_alli, value, attacker, a_alli in kills:
        session.add(Killmail(killmail_id=km_id, killmail_time=T, solar_system_id=sid,
                             victim_character_id=victim, victim_alliance_id=v_alli,
                             victim_ship_type_id=1, total_value=value,
                             npc_kill=False, solo_kill=False))
        await session.flush()
        session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=0, character_id=attacker,
                                     alliance_id=a_alli, damage_done=1, final_blow=True))
        session.add(FightKill(fight_id=fight.fight_id, killmail_id=km_id, side_idx=0))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="demo", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=now))
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.flush()
    return br_id, fight.fight_id


async def test_headline_only_counts_kills_a_friendly_pilot_was_on(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.sides_config import recompute_br_outcome

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        out = await recompute_br_outcome(
            session, br_id, baseline_alliances={NV}, baseline_corps=set()
        )
    assert out["our_isk_destroyed"] == pytest.approx(10.0)  # not 10 + 100 + 7
    assert out["our_isk_lost"] == pytest.approx(5.0)
    assert out["result"] == "win"


async def test_character_override_moves_a_loss_onto_our_side(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.sides_config import recompute_br_outcome

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        session.add(BrCharSide(br_id=br_id, character_id=LONER, side="friendly",
                               set_by_user="fc", set_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        out = await recompute_br_outcome(
            session, br_id, baseline_alliances={NV}, baseline_corps=set()
        )
    assert out["our_isk_lost"] == pytest.approx(5.0 + 7.0)


async def test_resolver_precedence(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.sides_config import SideResolver

    r = SideResolver(
        baseline_alliances={NV}, baseline_corps=set(),
        overrides={("alliance", HOSTILE): "hostile", ("alliance", NV): "hostile"},
        char_sides={LONER: "friendly", HOSTILE_PILOT: "friendly"},
    )
    assert r.entity(THIRD_A, None) == "unassigned"
    assert r.entity(HOSTILE, None) == "hostile"
    assert r.entity(NV, None) == "hostile"  # FC override beats the baseline
    assert r.character(LONER, None, None) == "friendly"
    assert r.character(HOSTILE_PILOT, HOSTILE, None) == "friendly"  # character beats entity
    assert r.character(None, THIRD_A, None) == "unassigned"


async def test_kill_markers_and_leader_sides_honour_character_overrides(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import _build_char_side_map, build_kill_events
    from app.analytics.sides_config import load_side_resolver

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)
        session.add(BrCharSide(br_id=br_id, character_id=LONER, side="friendly",
                               set_by_user="fc", set_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        resolver = await load_side_resolver(
            session, br_id, baseline_alliances={NV}, baseline_corps=set()
        )
        kills = {k.killmail_id: k for k in await build_kill_events(session, [fight_id], resolver)}
        sides = await _build_char_side_map(session, [fight_id], resolver)
    assert kills[4].side_kind == "friendly"
    assert kills[2].side_kind == "unassigned"
    assert sides[LONER] == "friendly"
    assert sides[NV_PILOT] == "friendly"
    assert sides[HOSTILE_PILOT] == "hostile"


async def test_setting_a_character_side_recomputes_the_headline(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    async with get_sessionmaker(settings)() as session:
        br_id, _ = await _seed(session)
        await session.commit()

    with TestClient(create_app()) as client:
        r = client.put(f"/api/brs/{br_id}/participants/{LONER}/side",
                       headers=CREATOR_HEADERS, json={"side": "friendly"})
        assert r.status_code == 200
        br = client.get(f"/api/brs/{br_id}", headers=CREATOR_HEADERS).json()
        assert br["our_isk_lost"] == pytest.approx(12.0)
        assert br["our_isk_destroyed"] == pytest.approx(10.0)
    reset_engine_for_tests()
