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
    from app.db.models import Killmail
    from app.ingest.persist import persist_killmails
    from sqlalchemy import select

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
        row = (await session.execute(select(Killmail).where(Killmail.killmail_id == 111))).scalar_one()
    assert row.total_value == pytest.approx(1500000.0)
