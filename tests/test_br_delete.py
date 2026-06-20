"""Tests for FC/HC battle-report deletion: cascade cleanup + access gating."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    Alliance,
    BattleReport,
    BrFight,
    BrKillmail,
    BrShipCount,
    BrSideOverride,
    BrSource,
    Character,
    Fight,
    FightKill,
    FightShipCount,
    FightSide,
    GamelogFile,
    InventoryType,
    Killmail,
    KillmailAttacker,
    LogEvent,
    LogEventBucket,
    SolarSystem,
)
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

NOW = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
ALLI = 99006113


async def _seed_full_br(session, *, br_id: str, fight_id_holder: dict, km_id: int, fid_key: str):  # type: ignore[no-untyped-def]
    """Seed one BR with a fight, killmail, sides, ship counts, overrides, a source,
    and a stamped log + bucket. Returns nothing; records the fight_id in the holder."""
    fight = Fight(system_id=31040404, started_at=NOW, ended_at=NOW,
                  isk_destroyed_total=1.0, largest_side_pilots=1,
                  capitals_involved=False, distinct_alliance_count=1)
    session.add(fight)
    await session.flush()
    fid = fight.fight_id
    fight_id_holder[fid_key] = fid

    session.add(Killmail(killmail_id=km_id, killmail_time=NOW, solar_system_id=31040404,
                         victim_character_id=10, victim_corporation_id=None,
                         victim_alliance_id=ALLI, victim_ship_type_id=1, total_value=1.0))
    await session.flush()
    session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=0, character_id=11,
                                 corporation_id=None, alliance_id=ALLI, ship_type_id=1,
                                 damage_done=1, final_blow=True))
    session.add(FightKill(fight_id=fid, killmail_id=km_id, side_idx=0))
    session.add(FightSide(fight_id=fid, side_idx=0, pilot_count=1, isk_lost=1.0, side_kind="friendly"))
    session.add(FightShipCount(fight_id=fid, side_idx=0, ship_type_id=1, count=1))

    session.add(BattleReport(br_id=br_id, source="t", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100, created_at=NOW))
    session.add(BrFight(br_id=br_id, fight_id=fid, seq=0))
    session.add(BrKillmail(br_id=br_id, killmail_id=km_id))
    session.add(BrShipCount(br_id=br_id, side_kind="friendly", ship_type_id=1, count=1))
    session.add(BrSideOverride(br_id=br_id, entity_type="alliance", entity_id=ALLI, side="friendly"))
    session.add(BrSource(br_id=br_id, kind="link", url="https://zkillboard.com/related/1/1/",
                         status="ready", km_count=1, created_at=NOW))

    gf = GamelogFile(uploaded_by_user="u", claimed_character_id=11, resolved_via="filename",
                     stored_path="/x", sha256=f"sha-{br_id}", mime="text/plain", size=1,
                     parse_status="parsed", event_count=1, uploaded_at=NOW)
    session.add(gf)
    await session.flush()
    session.add(LogEvent(file_id=gf.file_id, character_id=11, ts=NOW, effect_type="damage",
                         direction="out", amount=1.0, other_name="x", fight_id=fid))
    session.add(LogEventBucket(fight_id=fid, character_id=11, bucket_ts=NOW,
                               effect_type="damage", direction="out", sum_amount=1.0, event_count=1))


async def _count(session, model) -> int:  # type: ignore[no-untyped-def]
    return (await session.execute(select(func.count()).select_from(model))).scalar() or 0


@pytest.mark.asyncio
async def test_delete_br_cascade_removes_scoped_data_keeps_logs_and_other_brs(db_session_maker):
    from app.api.brs import _delete_br_cascade

    holder: dict[str, int] = {}
    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31040404, name="J-Del", security=None))
        session.add(Alliance(alliance_id=ALLI, name="No Vacancies.", last_seen_at=NOW))
        session.add(Character(character_id=10, name="Victim", last_seen_at=NOW))
        session.add(Character(character_id=11, name="Pilot", last_seen_at=NOW))
        session.add(InventoryType(type_id=1, name="TestShip"))
        await session.flush()
        await _seed_full_br(session, br_id="br-del", fight_id_holder=holder, km_id=5001, fid_key="del")
        await _seed_full_br(session, br_id="br-keep", fight_id_holder=holder, km_id=5002, fid_key="keep")
        await session.commit()

    async with db_session_maker() as session:
        await _delete_br_cascade(session, "br-del")
        await session.commit()

    async with db_session_maker() as session:
        # Target BR and all its scoped rows are gone.
        assert (await session.execute(
            select(BattleReport).where(BattleReport.br_id == "br-del"))).scalar_one_or_none() is None
        for model in (BrFight, BrKillmail, BrShipCount, BrSideOverride, BrSource):
            rows = (await session.execute(
                select(model).where(model.br_id == "br-del"))).scalars().all()
            assert rows == [], f"{model.__name__} not cleaned for br-del"

        # Its fight + cascaded children gone; its killmail + attacker gone.
        del_fid = holder["del"]
        assert (await session.execute(
            select(Fight).where(Fight.fight_id == del_fid))).scalar_one_or_none() is None
        assert (await session.execute(
            select(FightKill).where(FightKill.fight_id == del_fid))).scalars().all() == []
        assert (await session.execute(
            select(FightSide).where(FightSide.fight_id == del_fid))).scalars().all() == []
        assert (await session.execute(
            select(Killmail).where(Killmail.killmail_id == 5001))).scalar_one_or_none() is None
        assert (await session.execute(
            select(KillmailAttacker).where(KillmailAttacker.killmail_id == 5001))).scalars().all() == []
        # Derived bucket gone.
        assert (await session.execute(
            select(LogEventBucket).where(LogEventBucket.fight_id == del_fid))).scalars().all() == []

        # Raw log retained but un-stamped (re-association possible later).
        logs = (await session.execute(
            select(LogEvent).where(LogEvent.file_id.in_(
                select(GamelogFile.file_id).where(GamelogFile.sha256 == "sha-br-del"))))).scalars().all()
        assert len(logs) == 1 and logs[0].fight_id is None
        assert (await session.execute(
            select(GamelogFile).where(GamelogFile.sha256 == "sha-br-del"))).scalar_one_or_none() is not None

        # The OTHER BR is completely intact.
        assert (await session.execute(
            select(BattleReport).where(BattleReport.br_id == "br-keep"))).scalar_one_or_none() is not None
        keep_fid = holder["keep"]
        assert (await session.execute(
            select(Fight).where(Fight.fight_id == keep_fid))).scalar_one_or_none() is not None
        assert (await session.execute(
            select(Killmail).where(Killmail.killmail_id == 5002))).scalar_one_or_none() is not None

        # Shared reference data untouched.
        assert await _count(session, Alliance) == 1
        assert await _count(session, Character) == 2


async def _seed_min_br(session, br_id: str) -> None:  # type: ignore[no-untyped-def]
    session.add(BattleReport(br_id=br_id, source="t", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100, created_at=NOW))
    await session.flush()


@pytest.mark.asyncio
async def test_delete_br_api_gating(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        await _seed_min_br(session, "br-gate")
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # Member cannot delete.
        assert client.delete("/api/brs/br-gate", headers=MEMBER_HEADERS).status_code == 403
        # Still there.
        assert client.get("/api/brs/br-gate", headers=MEMBER_HEADERS).status_code == 200
        # FC/HC can delete.
        assert client.delete("/api/brs/br-gate", headers=CREATOR_HEADERS).status_code == 204
        # Gone now.
        assert client.get("/api/brs/br-gate", headers=CREATOR_HEADERS).status_code == 404
        # Deleting a missing BR → 404.
        assert client.delete("/api/brs/nope", headers=CREATOR_HEADERS).status_code == 404

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()
