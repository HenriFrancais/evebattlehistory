"""BrCharShip per-character ship override model."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_br_char_ship_roundtrip(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import BattleReport, BrCharShip

    br_id = str(uuid.uuid4())
    async with db_session_maker() as session:
        session.add(
            BattleReport(
                br_id=br_id, source="demo", source_url="http://x", source_ref="r",
                created_by_user="t", status="ready", progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(
            BrCharShip(
                br_id=br_id, character_id=42, ship_type_id=11987,
                set_by_user="fc", set_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    async with db_session_maker() as session:
        row = (
            await session.execute(
                select(BrCharShip).where(
                    BrCharShip.br_id == br_id, BrCharShip.character_id == 42
                )
            )
        ).scalar_one()
    assert row.ship_type_id == 11987
    assert row.set_by_user == "fc"
