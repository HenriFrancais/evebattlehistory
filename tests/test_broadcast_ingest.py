"""Ingest + association + reparse + supersession tests for fleet broadcasts."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import (
    BattleReport,
    Broadcast,
    BroadcastFile,
    BrFight,
    Character,
    Fight,
    SolarSystem,
)
from app.logs.broadcast_ingest import delete_broadcast_file, ingest_broadcast
from app.logs.broadcast_reparse import reparse_broadcasts

FIXTURE = Path(__file__).parent / "fixtures" / "broadcasts" / "sample_broadcast.txt"
RAW = FIXTURE.read_bytes()
BR_ID = "HSvLZHX"


def _settings(tmp_path):
    return SimpleNamespace(log_dir=tmp_path / "logs", max_log_mb=50)


async def _seed(session, *, two_fights: bool = True) -> None:
    session.add(SolarSystem(system_id=1, name="J1"))
    session.add(
        BattleReport(
            br_id=BR_ID, source="x", source_url="", source_ref="", created_by_user="u",
            created_at=dt.datetime(2026, 7, 12, 2, 0),
            battle_at=dt.datetime(2026, 7, 12, 0, 0),
        )
    )
    # Fight 1 ends just before midnight; Fight 2 opens at midnight (crosses the wrap).
    # Their ±120s padded windows leave a clean boundary so broadcasts split across both.
    session.add(Fight(fight_id=1, system_id=1,
                      started_at=dt.datetime(2026, 7, 11, 23, 50),
                      ended_at=dt.datetime(2026, 7, 11, 23, 59)))
    session.add(BrFight(br_id=BR_ID, fight_id=1, seq=0))
    if two_fights:
        session.add(Fight(fight_id=2, system_id=1,
                          started_at=dt.datetime(2026, 7, 12, 0, 0),
                          ended_at=dt.datetime(2026, 7, 12, 0, 5)))
        session.add(BrFight(br_id=BR_ID, fight_id=2, seq=1))
    # A friendly whose rep broadcast should resolve to a character id.
    session.add(Character(character_id=555, name="Alice Pilot",
                          last_seen_at=dt.datetime(2026, 7, 12, 0, 0)))


@pytest.mark.asyncio
async def test_ingest_parses_dates_and_resolves_subjects(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        res = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        assert res.broadcast_count == 7
        # Alice Pilot resolves via the Character fallback.
        alice = (await s.execute(
            select(Broadcast).where(Broadcast.subject_name == "Alice Pilot")
        )).scalars().first()
        assert alice.subject_character_id == 555
        # Enemy target subjects are never resolved.
        enemy = (await s.execute(
            select(Broadcast).where(Broadcast.kind == "target")
        )).scalars().first()
        assert enemy.subject_character_id is None
        # Dates reconstructed across midnight: newest 00:02:10 on 07-12.
        newest = (await s.execute(select(func.max(Broadcast.ts)))).scalar_one()
        assert newest.replace(tzinfo=None) == dt.datetime(2026, 7, 12, 0, 2, 10)


@pytest.mark.asyncio
async def test_association_splits_across_fights(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        total = (await s.execute(select(func.count()).select_from(Broadcast))).scalar_one()
        stamped = (await s.execute(
            select(func.count()).select_from(Broadcast).where(Broadcast.fight_id.is_not(None))
        )).scalar_one()
        f1 = (await s.execute(
            select(func.count()).select_from(Broadcast).where(Broadcast.fight_id == 1)
        )).scalar_one()
        f2 = (await s.execute(
            select(func.count()).select_from(Broadcast).where(Broadcast.fight_id == 2)
        )).scalar_one()
        assert total == 7
        assert stamped == 7
        assert f1 > 0 and f2 > 0 and f1 + f2 == 7


@pytest.mark.asyncio
async def test_gap_broadcast_keeps_br_but_no_fight(db_session_maker, tmp_path):
    # A single fight far from the broadcast window: nothing stamped, but rows persist by br_id.
    async with db_session_maker() as s:
        session_seed = _seed(s, two_fights=False)
        await session_seed
        # Move fight 1 far away so no broadcast overlaps (± 120s).
        f = (await s.execute(select(Fight).where(Fight.fight_id == 1))).scalar_one()
        f.started_at = dt.datetime(2026, 7, 12, 12, 0)
        f.ended_at = dt.datetime(2026, 7, 12, 12, 30)
        await s.commit()
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        rows = (await s.execute(select(func.count()).select_from(Broadcast))).scalar_one()
        stamped = (await s.execute(
            select(func.count()).select_from(Broadcast).where(Broadcast.fight_id.is_not(None))
        )).scalar_one()
        assert rows == 7
        assert stamped == 0


@pytest.mark.asyncio
async def test_sha_dedupe_returns_duplicate(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        r1 = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        r2 = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        assert r1.duplicate is False
        assert r2.duplicate is True
        files = (await s.execute(select(func.count()).select_from(BroadcastFile))).scalar_one()
        assert files == 1


@pytest.mark.asyncio
async def test_reupload_supersedes_prior(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        # First upload: a shorter log (fewer broadcasts).
        short = b"00:02:10 - Target Enemy One (Rattlesnake)\n"
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "short.txt", short, lambda n: None)
        await s.commit()
        # Second upload: the full (more complete) log — supersedes the short one.
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "full.txt", RAW, lambda n: None)
        await s.commit()
        files = (await s.execute(select(BroadcastFile).order_by(BroadcastFile.broadcast_count))).scalars().all()
        assert len(files) == 2
        superseded = [f for f in files if f.superseded]
        canonical = [f for f in files if not f.superseded]
        assert len(canonical) == 1
        assert canonical[0].broadcast_count == 7  # the fuller file wins
        assert len(superseded) == 1


@pytest.mark.asyncio
async def test_reparse_reproduces_rows(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        before = (await s.execute(select(func.count()).select_from(Broadcast))).scalar_one()
        n = await reparse_broadcasts(s, _settings(tmp_path))
        await s.commit()
        after = (await s.execute(select(func.count()).select_from(Broadcast))).scalar_one()
        assert n == 1
        assert after == before == 7


@pytest.mark.asyncio
async def test_delete_removes_rows_and_recanonicalises(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        res = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        br = await delete_broadcast_file(s, res.file_id)
        await s.commit()
        assert br == BR_ID
        rows = (await s.execute(select(func.count()).select_from(Broadcast))).scalar_one()
        files = (await s.execute(select(func.count()).select_from(BroadcastFile))).scalar_one()
        assert rows == 0 and files == 0


@pytest.mark.asyncio
async def test_ingest_requires_anchor(db_session_maker, tmp_path):
    # A BR with no fights and no battle_at cannot anchor dates → ValueError.
    async with db_session_maker() as s:
        s.add(BattleReport(br_id="EMPTY", source="x", source_url="", source_ref="",
                           created_by_user="u", created_at=dt.datetime(2026, 7, 12, 2, 0),
                           battle_at=None))
        await s.commit()
        with pytest.raises(ValueError):
            await ingest_broadcast(s, _settings(tmp_path), "EMPTY", "fc", "bc.txt", RAW, lambda n: None)


# ---------------------------------------------------------------------------
# Same file on two BRs, reparse keeps roster-resolved ids, stored files cleaned up
# ---------------------------------------------------------------------------

BR_TWO = "SecondBR"


async def _seed_second_br(session) -> None:
    session.add(
        BattleReport(
            br_id=BR_TWO, source="x", source_url="", source_ref="", created_by_user="u",
            created_at=dt.datetime(2026, 7, 12, 2, 0),
            battle_at=dt.datetime(2026, 7, 12, 0, 0),
        )
    )
    session.add(BrFight(br_id=BR_TWO, fight_id=1, seq=0))


def _stored(tmp_path) -> list[Path]:
    d = tmp_path / "logs"
    return sorted(d.iterdir()) if d.exists() else []


@pytest.mark.asyncio
async def test_same_file_can_be_attached_to_a_second_br(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await _seed_second_br(s)
        await s.commit()
        r1 = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        r2 = await ingest_broadcast(s, _settings(tmp_path), BR_TWO, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        assert (r1.duplicate, r2.duplicate) == (False, False)
        assert r2.br_id == BR_TWO
        per_br = dict((await s.execute(
            select(Broadcast.br_id, func.count()).group_by(Broadcast.br_id)
        )).all())
        assert per_br == {BR_ID: 7, BR_TWO: 7}
        # Re-uploading to the SAME br is still a duplicate.
        r3 = await ingest_broadcast(s, _settings(tmp_path), BR_TWO, "fc", "bc.txt", RAW, lambda n: None)
        assert r3.duplicate is True and r3.br_id == BR_TWO
    assert len(_stored(tmp_path)) == 1  # content stored once


@pytest.mark.asyncio
async def test_reparse_keeps_roster_resolved_subject_ids(db_session_maker, tmp_path):
    # "Bob Logi" is known ONLY to the roster (no Character row) at upload time.
    lookup = {"bob logi": 777}
    raw = b"00:02:10 - Bob Logi needs armor (Guardian)\n"
    async with db_session_maker() as s:
        await _seed(s)
        await s.commit()
        await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "b.txt", raw,
                               lambda n: lookup.get(n.lower()))
        await s.commit()
        await reparse_broadcasts(s, _settings(tmp_path))
        await s.commit()
        cid = (await s.execute(select(Broadcast.subject_character_id))).scalar_one()
        assert cid == 777


@pytest.mark.asyncio
async def test_delete_unlinks_the_stored_file_unless_still_referenced(db_session_maker, tmp_path):
    async with db_session_maker() as s:
        await _seed(s)
        await _seed_second_br(s)
        await s.commit()
        r1 = await ingest_broadcast(s, _settings(tmp_path), BR_ID, "fc", "bc.txt", RAW, lambda n: None)
        r2 = await ingest_broadcast(s, _settings(tmp_path), BR_TWO, "fc", "bc.txt", RAW, lambda n: None)
        await s.commit()
        await delete_broadcast_file(s, r1.file_id)
        await s.commit()
        assert len(_stored(tmp_path)) == 1, "still used by the second BR"
        await delete_broadcast_file(s, r2.file_id)
        await s.commit()
    assert _stored(tmp_path) == []
