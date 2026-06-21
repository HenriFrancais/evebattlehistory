"""Tests that persist_killmails writes victim.damage_taken to the Killmail row."""

import pytest
from sqlalchemy import select

from app.db.models import Killmail
from app.ingest.persist import persist_killmails


@pytest.mark.asyncio
async def test_persist_writes_damage_taken(db_session_maker):
    raw = {
        "killmail_id": 77,
        "killmail_time": "2026-06-10T20:00:00Z",
        "solar_system_id": 31002222,
        "victim": {"ship_type_id": 645, "damage_taken": 99999},
        "attackers": [],
        "zkb": {},
    }
    async with db_session_maker() as s:
        await persist_killmails(s, [raw], names={}, values=None)
        await s.commit()
    async with db_session_maker() as s:
        dt_ = (
            await s.execute(
                select(Killmail.damage_taken).where(Killmail.killmail_id == 77)
            )
        ).scalar_one()
    assert dt_ == 99999
