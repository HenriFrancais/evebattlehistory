"""Two battle reports covering the same engagement must show the same logs.

A LogEvent carries one fight_id, so the fix is to make the FIGHT shared: when a
BR's clustered fight has exactly the killmails of an existing fight, the BR links
to that fight instead of minting a competing copy. Re-aggregating keeps fight ids
stable, so logs never migrate between reports on refresh.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tests.conftest import CREATOR_HEADERS
from tests.test_fights import _setup_demo_db

BR_A = "demo-br-001"
BR_B = "demo-br-002"
CHAR = 2100000001
IN_FIGHT = dt.datetime(2026, 6, 10, 20, 20, 0)


async def _aggregate(session_maker, br_id: str) -> None:  # type: ignore[no-untyped-def]
    from app.fights.aggregate import aggregate_br
    from app.logs.associate import associate_logs_for_br

    async with session_maker() as session:
        await aggregate_br(session, br_id=br_id, our_alliance_ids=[99000001], our_corp_ids=[])
        await associate_logs_for_br(session, br_id)
        await session.commit()


async def _clone_br(session_maker, drop_km: int | None = None) -> None:  # type: ignore[no-untyped-def]
    """Create BR_B over the same killmails as BR_A (optionally minus one)."""
    from app.db.models import BattleReport, BrKillmail

    async with session_maker() as session:
        km_ids = list(
            (await session.execute(
                select(BrKillmail.killmail_id).where(BrKillmail.br_id == BR_A)
            )).scalars()
        )
        session.add(BattleReport(
            br_id=BR_B, source="demo", source_url="demo://demo2", source_ref="demo",
            created_by_user="test", status="done", created_at=dt.datetime.now(dt.UTC),
        ))
        await session.flush()
        await session.execute(sqlite_insert(BrKillmail).values(
            [{"br_id": BR_B, "killmail_id": k} for k in km_ids if k != drop_km]
        ))
        await session.commit()


async def _add_log(session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import GamelogFile, LogEvent

    async with session_maker() as session:
        gf = GamelogFile(
            uploaded_by_user="test", claimed_character_id=CHAR, character_name="T",
            original_filename="t.txt", resolved_via="filename",
            log_start_at=IN_FIGHT - dt.timedelta(minutes=5),
            log_end_at=IN_FIGHT + dt.timedelta(minutes=5),
            stored_path="/tmp/t.txt", sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            mime="text/plain", size=1, parse_status="parsed", event_count=1,
            uploaded_at=dt.datetime.now(dt.UTC),
        )
        session.add(gf)
        await session.flush()
        session.add(LogEvent(
            file_id=gf.file_id, character_id=CHAR, ts=IN_FIGHT, direction="out",
            effect_type="damage", amount=100.0, fight_id=None,
        ))
        await session.commit()


async def _fight_ids(session_maker, br_id: str) -> list[int]:  # type: ignore[no-untyped-def]
    from app.db.models import BrFight

    async with session_maker() as session:
        return sorted(
            (await session.execute(
                select(BrFight.fight_id).where(BrFight.br_id == br_id)
            )).scalars()
        )


async def _events_in(session_maker, fight_ids: list[int]) -> int:  # type: ignore[no-untyped-def]
    from app.db.models import LogEvent

    async with session_maker() as session:
        return int((await session.execute(
            select(func.count()).select_from(LogEvent).where(LogEvent.fight_id.in_(fight_ids))
        )).scalar_one())


async def test_two_brs_over_the_same_kills_share_the_fight_and_its_logs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sm = await _setup_demo_db(tmp_path)
    await _clone_br(sm)
    await _add_log(sm)
    await _aggregate(sm, BR_A)
    await _aggregate(sm, BR_B)

    a, b = await _fight_ids(sm, BR_A), await _fight_ids(sm, BR_B)
    assert a and a == b, "same killmails must resolve to the same Fight row"
    assert await _events_in(sm, a) == 1
    assert await _events_in(sm, b) == 1


async def test_reaggregating_keeps_fight_ids_and_log_stamps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.fights.aggregate import aggregate_br

    sm = await _setup_demo_db(tmp_path)
    await _add_log(sm)
    await _aggregate(sm, BR_A)
    before = await _fight_ids(sm, BR_A)

    async with sm() as session:  # refresh WITHOUT re-associating
        await aggregate_br(session, br_id=BR_A, our_alliance_ids=[99000001], our_corp_ids=[])
        await session.commit()

    assert await _fight_ids(sm, BR_A) == before
    assert await _events_in(sm, before) == 1


async def test_refreshing_one_br_does_not_take_logs_from_the_other(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sm = await _setup_demo_db(tmp_path)
    await _clone_br(sm)
    await _add_log(sm)
    await _aggregate(sm, BR_A)
    await _aggregate(sm, BR_B)
    await _aggregate(sm, BR_A)  # refresh A again
    await _aggregate(sm, BR_A)

    assert await _events_in(sm, await _fight_ids(sm, BR_B)) == 1
    assert await _events_in(sm, await _fight_ids(sm, BR_A)) == 1


async def test_deleting_one_br_keeps_the_shared_fight_for_the_other(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.api.brs import _delete_br_cascade
    from app.db.models import Fight

    sm = await _setup_demo_db(tmp_path)
    await _clone_br(sm)
    await _add_log(sm)
    await _aggregate(sm, BR_A)
    await _aggregate(sm, BR_B)
    shared = await _fight_ids(sm, BR_B)

    async with sm() as session:
        await _delete_br_cascade(session, BR_A)
        await session.commit()

    async with sm() as session:
        alive = (await session.execute(
            select(func.count()).select_from(Fight).where(Fight.fight_id.in_(shared))
        )).scalar_one()
    assert alive == len(shared)
    assert await _events_in(sm, shared) == 1


async def test_a_br_with_a_different_kill_set_gets_its_own_fight(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sm = await _setup_demo_db(tmp_path)
    await _clone_br(sm, drop_km=105)
    await _aggregate(sm, BR_A)
    await _aggregate(sm, BR_B)
    a, b = await _fight_ids(sm, BR_A), await _fight_ids(sm, BR_B)
    assert a and b and set(a).isdisjoint(b)


def test_creating_a_br_for_an_existing_source_is_refused(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = make_client(DB_PATH=str(tmp_path / "test.db"))
    url = "https://zkillboard.com/related/30002222/202606101500/"
    first = client.post("/api/brs", json={"url": url}, headers=CREATOR_HEADERS)
    assert first.status_code == 202
    again = client.post(
        "/api/brs", json={"url": url.replace("https://", "https://www.")},
        headers=CREATOR_HEADERS,
    )
    assert again.status_code == 409
    assert first.json()["br_id"] in again.json()["detail"]

    forced = client.post(
        "/api/brs", json={"url": url, "allow_duplicate": True}, headers=CREATOR_HEADERS
    )
    assert forced.status_code == 202
