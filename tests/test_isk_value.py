from __future__ import annotations

import datetime as dt

import pytest


def test_extract_refs_captures_total_value():
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    data = {
        "summary": {
            "teamA": {"kills": {
                "111": {"zkb": {"hash": "h1", "totalValue": 1500000.0}},
            }},
            "teamB": {"kills": {
                "222": {"zkb": {"hash": "h2"}},  # no value
            }},
        }
    }
    refs, values = _extract_refs_from_related(data)
    assert refs == [(111, "h1"), (222, "h2")]
    assert values == {111: 1500000.0, 222: None}


@pytest.mark.asyncio
async def test_persist_injects_total_value(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.db.models import Killmail
    from app.ingest.persist import persist_killmails

    km = {
        "killmail_id": 111,
        "killmail_time": dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
        "solar_system_id": 31002222,
        "victim": {"character_id": 5, "ship_type_id": 670},
        "attackers": [],
    }
    async with db_session_maker() as session:
        await persist_killmails(session, [km], {}, values={111: 1500000.0})
        await session.commit()

    async with db_session_maker() as session:
        row = (
            await session.execute(select(Killmail).where(Killmail.killmail_id == 111))
        ).scalar_one()
    assert row.total_value == pytest.approx(1500000.0)


@pytest.mark.asyncio
async def test_persist_refresh_backfills_existing_null_value(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A refresh fills a previously-null existing killmail's total_value."""
    from sqlalchemy import select

    from app.db.models import Killmail
    from app.ingest.persist import persist_killmails

    km = {
        "killmail_id": 333,
        "killmail_time": dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
        "solar_system_id": 31002222,
        "victim": {"character_id": 5, "ship_type_id": 670},
        "attackers": [],
    }
    # First persist with no value → row exists with total_value=None.
    async with db_session_maker() as session:
        await persist_killmails(session, [km], {}, values=None)
        await session.commit()

    async with db_session_maker() as session:
        row = (
            await session.execute(select(Killmail).where(Killmail.killmail_id == 333))
        ).scalar_one()
        assert row.total_value is None

    # Refresh: same killmail already present, but now a value is known.
    async with db_session_maker() as session:
        await persist_killmails(session, [km], {}, values={333: 7700000.0})
        await session.commit()

    async with db_session_maker() as session:
        row = (
            await session.execute(select(Killmail).where(Killmail.killmail_id == 333))
        ).scalar_one()
    assert row.total_value == pytest.approx(7700000.0)


@pytest.mark.asyncio
async def test_backfill_fills_null_values(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import uuid

    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import (
        BattleReport,
        BrKillmail,
        InventoryType,
        Killmail,
        SolarSystem,
    )
    from app.ingest import zkb_value

    # The fixture runs with DATA_SOURCE=demo (which the backfill skips), so pass a
    # real-mode settings copy to exercise the actual backfill path.
    settings = get_settings().model_copy(update={"data_source": "real"})

    async with db_session_maker() as session:
        br_id = str(uuid.uuid4())
        # FK enforcement is ON, so seed the killmail's parent rows.
        session.add(SolarSystem(system_id=31002222, name="J100222"))
        session.add(InventoryType(type_id=670, name="Capsule"))
        session.add(BattleReport(br_id=br_id, source="zkb", source_url="http://x", source_ref="r",
                                 created_by_user="t", status="ready", progress_pct=100,
                                 created_at=dt.datetime.now(dt.UTC)))
        session.add(Killmail(killmail_id=900, killmail_time=dt.datetime(2026, 6, 10, tzinfo=dt.UTC),
                             solar_system_id=31002222, victim_ship_type_id=670,
                             total_value=None, npc_kill=False, solo_kill=False, hash="hh"))
        session.add(BrKillmail(br_id=br_id, killmail_id=900))
        await session.commit()

    async def fake_fetch(client, km_id):
        return 4242.0 if km_id == 900 else None

    monkeypatch.setattr(zkb_value, "_fetch_value", fake_fetch)

    async with db_session_maker() as session:
        n = await zkb_value.backfill_killmail_values(session, br_id, settings)
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        row = (
            await session.execute(select(Killmail).where(Killmail.killmail_id == 900))
        ).scalar_one()
    assert row.total_value == pytest.approx(4242.0)
