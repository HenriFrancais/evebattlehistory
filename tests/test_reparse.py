import datetime as dt
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_reparse_replaces_events_with_split(  # type: ignore[no-untyped-def]
    db_session_maker, tmp_path: Path
) -> None:
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import GamelogFile, InventoryType, LogEvent
    from app.logs.reparse import reparse_gamelogs

    p = tmp_path / "g.txt"
    p.write_text(
        "[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        "Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact "
        "Remote Capacitor Transmitter\n",
        encoding="utf-8",
    )
    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        gf = GamelogFile(
            uploaded_by_user="u", claimed_character_id=90000001, resolved_via="filename",
            stored_path=str(p), sha256="rr", mime="text/plain", size=1,
            parse_status="parsed", event_count=0, uploaded_at=dt.datetime.now(dt.UTC),
        )
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


@pytest.mark.asyncio
async def test_reparse_clears_stale_buckets_for_unmatched_fight(  # type: ignore[no-untyped-def]
    db_session_maker, tmp_path: Path
) -> None:
    """Re-parsing a file whose new events no longer match a previously-associated
    fight must leave NO stale LogEventBucket rows for that fight."""
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.db.models import (
        Fight,
        GamelogFile,
        InventoryType,
        LogEvent,
        LogEventBucket,
        SolarSystem,
    )
    from app.logs.reparse import reparse_gamelogs

    CHAR = 90000001
    SYSTEM = 31002222
    # The fight sits in a time window with NO overlap with the file's event ts
    # (2026-06-14 20:57:34): the re-parsed events cannot re-match it.
    OLD_FIGHT_START = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.UTC)
    OLD_FIGHT_END = dt.datetime(2026, 1, 1, 0, 5, 0, tzinfo=dt.UTC)

    p = tmp_path / "g.txt"
    p.write_text(
        "[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        "Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact "
        "Remote Capacitor Transmitter\n",
        encoding="utf-8",
    )

    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        session.add(SolarSystem(system_id=SYSTEM, name="J-Test", security=None))
        await session.flush()
        fight = Fight(
            system_id=SYSTEM, started_at=OLD_FIGHT_START, ended_at=OLD_FIGHT_END,
            isk_destroyed_total=0.0, largest_side_pilots=1, capitals_involved=False,
            distinct_alliance_count=1,
        )
        session.add(fight)
        await session.flush()
        old_fight_id = fight.fight_id

        gf = GamelogFile(
            uploaded_by_user="u", claimed_character_id=CHAR, resolved_via="filename",
            stored_path=str(p), sha256="rr", mime="text/plain", size=1,
            parse_status="parsed", event_count=1, uploaded_at=dt.datetime.now(dt.UTC),
        )
        session.add(gf)
        await session.flush()
        # An existing event already stamped to the old fight, plus its bucket row —
        # exactly the state a prior associate pass would have produced.
        session.add(LogEvent(
            file_id=gf.file_id, character_id=CHAR, ts=OLD_FIGHT_START,
            effect_type="cap_transfer", direction="out", other_name="STALE",
            other_ship_name=None, fight_id=old_fight_id,
        ))
        session.add(LogEventBucket(
            fight_id=old_fight_id, character_id=CHAR, bucket_ts=OLD_FIGHT_START,
            effect_type="cap_transfer", direction="out", sum_amount=88.0, event_count=1,
        ))
        await session.commit()

    async with db_session_maker() as session:
        n = await reparse_gamelogs(session, get_settings())
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        stale = (
            await session.execute(
                select(func.count())
                .select_from(LogEventBucket)
                .where(LogEventBucket.fight_id == old_fight_id)
            )
        ).scalar_one()
    assert stale == 0, "stale buckets for the no-longer-matched fight must be cleared"
