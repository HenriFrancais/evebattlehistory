from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db.models import BattleReport, BrFight, Fight, SolarSystem


@pytest.mark.asyncio
async def test_br_detail_lists_distinct_system_names(make_client, db_session_maker) -> None:  # type: ignore[no-untyped-def]
    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31002222, name="J164805"))
        f = Fight(system_id=31002222, started_at=dt.datetime(2026, 6, 10, tzinfo=dt.UTC),
                  ended_at=dt.datetime(2026, 6, 10, tzinfo=dt.UTC), isk_destroyed_total=0.0,
                  largest_side_pilots=1)
        session.add(f)
        await session.flush()
        br_id = str(uuid.uuid4())
        session.add(BattleReport(br_id=br_id, source="demo", source_url="http://x", source_ref="r",
                                 created_by_user="t", status="ready", progress_pct=100,
                                 created_at=dt.datetime.now(dt.UTC)))
        session.add(BrFight(br_id=br_id, fight_id=f.fight_id, seq=0))
        await session.commit()

    detail = await _call_get_br(br_id, db_session_maker)
    assert detail.systems == ["J164805"]


async def _call_get_br(br_id, db_session_maker):  # type: ignore[no-untyped-def]
    from app.api.brs import get_br
    async with db_session_maker() as session:
        return await get_br(br_id, session)
