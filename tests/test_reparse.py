import datetime as dt
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_reparse_replaces_events_with_split(db_session_maker, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.models import GamelogFile, InventoryType, LogEvent
    from app.logs.reparse import reparse_gamelogs
    from sqlalchemy import select

    p = tmp_path / "g.txt"
    p.write_text(
        "[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        "Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact Remote Capacitor Transmitter\n",
        encoding="utf-8",
    )
    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=90000001, resolved_via="filename",
                         stored_path=str(p), sha256="rr", mime="text/plain", size=1,
                         parse_status="parsed", event_count=0, uploaded_at=dt.datetime.now(dt.UTC))
        session.add(gf)
        # a stale event that must be replaced
        await session.flush()
        session.add(LogEvent(file_id=gf.file_id, character_id=90000001,
                             ts=dt.datetime(2026, 6, 14, 20, 57, 34), effect_type="cap_transfer",
                             direction="out", other_name="STALE", other_ship_name=None))
        await session.commit()

    async with db_session_maker() as session:
        n = await reparse_gamelogs(session, get_settings())
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        evs = (await session.execute(select(LogEvent))).scalars().all()
    assert all(e.other_name != "STALE" for e in evs)
    cap = next(e for e in evs if e.effect_type == "cap_transfer")
    assert cap.other_ship_name == "Guardian" and cap.other_name == "Jennifer Hibra"
