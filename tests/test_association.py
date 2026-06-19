"""TDD tests for Task 2.3: log↔fight association + buckets + coverage.

Scenario
--------
* Two characters: char_a (2100000001) participated in fight_1; char_b (2200000001)
  is an attacker in the same fight.  char_c (9999999999) did NOT participate.
* fight_1: started_at=2026-06-10 20:00 UTC, ended_at=2026-06-10 20:30 UTC
  (matches demo killmail times).
* A gamelog for char_a covers 2026-06-10 19:55 UTC - 2026-06-10 20:35 UTC
  (overlaps with fight_1 +120s padding).
* A gamelog for char_c covers the same window but char_c is NOT a participant.

Decoupled-order tests:
  - Order A: log uploaded AFTER BR ingested → associate_file_to_all picks it up.
  - Order B: log uploaded BEFORE BR ingested → associate_logs_for_br picks it up.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import (
    BattleReport,
    BrFight,
    Character,
    Fight,
    FightKill,
    GamelogFile,
    InventoryType,
    Killmail,
    KillmailAttacker,
    LogEvent,
    LogEventBucket,
    SolarSystem,
)

# Character IDs used in this test module
CHAR_A = 2100000001  # victim + attacker (participant)
CHAR_B = 2200000001  # attacker (participant)
CHAR_C = 9999999999  # NOT a participant

# Arbitrary roster user
USER_A = "UserAlpha"
USER_B = "UserBeta"
USER_C = "UserGamma"

# Fight window
FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC)

# Log covers the fight window ± some slack
LOG_START = dt.datetime(2026, 6, 10, 19, 55, 0, tzinfo=dt.UTC)
LOG_END = dt.datetime(2026, 6, 10, 20, 35, 0, tzinfo=dt.UTC)

# Timestamps for events that should/should not be stamped
TS_INSIDE = dt.datetime(2026, 6, 10, 20, 15, 0, tzinfo=dt.UTC)  # inside fight window
TS_OUTSIDE = dt.datetime(2026, 6, 10, 18, 0, 0, tzinfo=dt.UTC)  # way before fight, outside pad

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SHIP_TYPE_ID = 1  # dummy ship type used in all test killmails


async def _ensure_inventory_type(session, type_id: int = _SHIP_TYPE_ID) -> None:  # type: ignore[no-untyped-def]
    result = await session.execute(
        select(InventoryType).where(InventoryType.type_id == type_id)
    )
    if result.scalar_one_or_none() is None:
        session.add(
            InventoryType(
                type_id=type_id,
                name="TestShip",
                group_id=0,
                group_name="Unknown",
                category_id=0,
                category_name="Unknown",
            )
        )
        await session.flush()


async def _insert_solar_system(session, system_id: int = 31002222) -> None:  # type: ignore[no-untyped-def]
    result = await session.execute(
        select(SolarSystem).where(SolarSystem.system_id == system_id)
    )
    if result.scalar_one_or_none() is None:
        session.add(SolarSystem(system_id=system_id, name="J-Test", security=None))
        await session.flush()


async def _insert_character(session, character_id: int) -> None:  # type: ignore[no-untyped-def]
    result = await session.execute(select(Character).where(Character.character_id == character_id))
    if result.scalar_one_or_none() is None:
        session.add(
            Character(
                character_id=character_id,
                name=f"Char{character_id}",
                last_seen_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.flush()


async def _insert_fight(  # type: ignore[no-untyped-def]
    session,
    system_id: int = 31002222,
    started_at: dt.datetime = FIGHT_START,
    ended_at: dt.datetime = FIGHT_END,
    victim_char_id: int = CHAR_A,
    attacker_char_id: int = CHAR_B,
) -> int:
    """Insert a Fight + one Killmail + FightKill + KillmailAttacker.  Returns fight_id."""
    await _ensure_inventory_type(session, _SHIP_TYPE_ID)
    await _insert_solar_system(session, system_id)
    await _insert_character(session, victim_char_id)
    await _insert_character(session, attacker_char_id)

    # Killmail
    km_id = int(time.monotonic_ns() % 2**30)
    km = Killmail(
        killmail_id=km_id,
        killmail_time=started_at,
        solar_system_id=system_id,
        victim_character_id=victim_char_id,
        victim_ship_type_id=_SHIP_TYPE_ID,
        npc_kill=False,
        solo_kill=False,
    )
    session.add(km)
    await session.flush()

    # Attacker
    session.add(
        KillmailAttacker(
            killmail_id=km_id,
            attacker_idx=0,
            character_id=attacker_char_id,
            damage_done=100,
            final_blow=True,
        )
    )

    # Fight
    fight = Fight(
        system_id=system_id,
        started_at=started_at,
        ended_at=ended_at,
        isk_destroyed_total=0.0,
        largest_side_pilots=2,
        capitals_involved=False,
        distinct_alliance_count=1,
    )
    session.add(fight)
    await session.flush()

    # FightKill
    session.add(FightKill(fight_id=fight.fight_id, killmail_id=km_id, side_idx=0))
    await session.flush()

    return fight.fight_id


async def _insert_gamelog_file(  # type: ignore[no-untyped-def]
    session,
    character_id: int | None,
    uploaded_by_user: str = USER_A,
    log_start: dt.datetime = LOG_START,
    log_end: dt.datetime = LOG_END,
    parse_status: str = "parsed",
) -> int:
    """Insert a minimal GamelogFile row.  Returns file_id."""
    sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    gf = GamelogFile(
        uploaded_by_user=uploaded_by_user,
        claimed_character_id=character_id,
        listener_name=None,
        character_name=f"Char{character_id}" if character_id else None,
        original_filename="test.txt",
        resolved_via="filename" if character_id else "unresolved",
        session_started_at=None,
        log_start_at=log_start,
        log_end_at=log_end,
        stored_path="/tmp/test.txt",
        sha256=sha,
        mime="text/plain",
        size=100,
        parse_status=parse_status,
        event_count=0,
        uploaded_at=dt.datetime.now(dt.UTC),
    )
    session.add(gf)
    await session.flush()
    return gf.file_id


async def _insert_log_events(  # type: ignore[no-untyped-def]
    session,
    file_id: int,
    character_id: int | None,
    timestamps: list[dt.datetime],
    effect_type: str = "damage",
    direction: str = "in",
    amount: float = 100.0,
) -> list[int]:
    """Insert LogEvent rows and return their event_ids."""
    event_ids = []
    for ts in timestamps:
        ev = LogEvent(
            file_id=file_id,
            character_id=character_id,
            ts=ts,
            direction=direction,
            effect_type=effect_type,
            amount=amount,
            fight_id=None,
        )
        session.add(ev)
        await session.flush()
        event_ids.append(ev.event_id)
    return event_ids


# ---------------------------------------------------------------------------
# 1. participation helper
# ---------------------------------------------------------------------------


async def test_fight_participant_char_ids_returns_victims_and_attackers(  # type: ignore[no-untyped-def]
    db_session_maker,
) -> None:
    """fight_participant_char_ids returns both victims and attackers."""
    from app.fights.participants import fight_participant_char_ids

    async with db_session_maker() as session:
        fight_id = await _insert_fight(
            session, victim_char_id=CHAR_A, attacker_char_id=CHAR_B
        )
        await session.commit()

    async with db_session_maker() as session:
        participants = await fight_participant_char_ids(session, fight_id)

    assert CHAR_A in participants
    assert CHAR_B in participants
    assert CHAR_C not in participants


async def test_fight_participant_char_ids_empty_fight(  # type: ignore[no-untyped-def]
    db_session_maker,
) -> None:
    """fight_participant_char_ids returns empty set for non-existent fight."""
    from app.fights.participants import fight_participant_char_ids

    async with db_session_maker() as session:
        participants = await fight_participant_char_ids(session, 999999)

    assert participants == set()


# ---------------------------------------------------------------------------
# 2. associate_file: basic stamping
# ---------------------------------------------------------------------------


async def test_associate_file_stamps_events_in_window(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Events inside the fight window are stamped; events outside are not."""
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        # One event inside the fight+pad window, one way outside
        inside_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        outside_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_OUTSIDE])
        await session.commit()

    async with db_session_maker() as session:
        count = await associate_file(session, file_id)
        await session.commit()

    async with db_session_maker() as session:
        inside_ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == inside_ids[0]))
        ).scalar_one()
        outside_ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == outside_ids[0]))
        ).scalar_one()

    assert count == 1
    assert inside_ev.fight_id == fight_id
    assert outside_ev.fight_id is None


async def test_associate_file_skips_unresolved_character(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Files with no character_id are skipped and 0 events stamped."""
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        await _insert_fight(session)
        file_id = await _insert_gamelog_file(
            session, character_id=None, parse_status="unresolved"
        )
        await _insert_log_events(session, file_id, None, [TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        count = await associate_file(session, file_id)
        await session.commit()

    assert count == 0


async def test_associate_file_no_overlap_not_stamped(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A log whose timestamps do NOT overlap any fight window gets no events stamped.

    E1 design change: association is time-window based, not participation based.
    The negative case is now "log outside fight window", not "non-participant".
    CHAR_C's log covers a window way before the fight — no fight_id stamped.
    """
    from app.logs.associate import associate_file

    # Log window that is way before the fight (no overlap)
    no_overlap_start = dt.datetime(2026, 6, 10, 15, 0, 0, tzinfo=dt.UTC)
    no_overlap_end = dt.datetime(2026, 6, 10, 16, 0, 0, tzinfo=dt.UTC)
    ts_no_overlap = dt.datetime(2026, 6, 10, 15, 30, 0, tzinfo=dt.UTC)

    async with db_session_maker() as session:
        await _insert_fight(session, victim_char_id=CHAR_A, attacker_char_id=CHAR_B)
        file_id = await _insert_gamelog_file(
            session,
            character_id=CHAR_C,
            log_start=no_overlap_start,
            log_end=no_overlap_end,
        )
        await _insert_log_events(session, file_id, CHAR_C, [ts_no_overlap])
        await session.commit()

    async with db_session_maker() as session:
        count = await associate_file(session, file_id)
        await session.commit()

    assert count == 0

    async with db_session_maker() as session:
        ev_count = (
            await session.execute(
                select(func.count())
                .select_from(LogEvent)
                .where(LogEvent.fight_id.is_not(None))
            )
        ).scalar()
    assert ev_count == 0


# ---------------------------------------------------------------------------
# 3. associate_file: idempotency
# ---------------------------------------------------------------------------


async def test_associate_file_idempotent(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Running associate_file twice produces identical fight_ids and bucket sums."""
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        await _insert_log_events(
            session, file_id, CHAR_A, [TS_INSIDE, TS_INSIDE], amount=50.0
        )
        await session.commit()

    # First run
    async with db_session_maker() as session:
        count1 = await associate_file(session, file_id)
        await session.commit()

    # Second run (idempotent)
    async with db_session_maker() as session:
        count2 = await associate_file(session, file_id)
        await session.commit()

    assert count1 == count2  # same number of events stamped

    async with db_session_maker() as session:
        # No duplicate buckets
        bucket_count = (
            await session.execute(
                select(func.count())
                .select_from(LogEventBucket)
                .where(LogEventBucket.fight_id == fight_id)
            )
        ).scalar()
        # Sum should equal 2 * 50.0 = 100.0
        bucket_sum = (
            await session.execute(
                select(func.sum(LogEventBucket.sum_amount))
                .where(LogEventBucket.fight_id == fight_id)
            )
        ).scalar()
        # Total event_count across all buckets
        total_event_count = (
            await session.execute(
                select(func.sum(LogEventBucket.event_count))
                .where(LogEventBucket.fight_id == fight_id)
            )
        ).scalar()

    # Only one bucket for this (fight, char, bucket_ts, effect_type, direction)
    assert bucket_count == 1
    assert bucket_sum == pytest.approx(100.0)
    assert total_event_count == 2


# ---------------------------------------------------------------------------
# 4. LogEventBucket construction
# ---------------------------------------------------------------------------


async def test_buckets_created_with_correct_sums(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """After association, buckets exist with correct sum_amount and event_count."""
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        # Three events in the same 5-second bucket (all at TS_INSIDE which is 20:15:00)
        await _insert_log_events(
            session, file_id, CHAR_A, [TS_INSIDE, TS_INSIDE, TS_INSIDE], amount=200.0
        )
        await session.commit()

    async with db_session_maker() as session:
        await associate_file(session, file_id)
        await session.commit()

    async with db_session_maker() as session:
        buckets = list(
            (
                await session.execute(
                    select(LogEventBucket).where(LogEventBucket.fight_id == fight_id)
                )
            ).scalars()
        )

    assert len(buckets) == 1
    b = buckets[0]
    assert b.character_id == CHAR_A
    assert b.sum_amount == pytest.approx(600.0)  # 3 * 200.0
    assert b.event_count == 3
    assert b.effect_type == "damage"
    assert b.direction == "in"


# ---------------------------------------------------------------------------
# 5. associate_logs_for_br
# ---------------------------------------------------------------------------


async def test_associate_logs_for_br_stamps_participant_files(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """associate_logs_for_br stamps events for uploaded participant files."""
    from app.logs.associate import associate_logs_for_br

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)

        # Link fight to a BR
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))

        # Upload file for CHAR_A (participant)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        ev_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        await associate_logs_for_br(session, br_id)
        await session.commit()

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == ev_ids[0]))
        ).scalar_one()

    assert ev.fight_id == fight_id


# ---------------------------------------------------------------------------
# 6. associate_file_to_all
# ---------------------------------------------------------------------------


async def test_associate_file_to_all_stamps_against_all_fights(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """associate_file_to_all associates a new file against all existing fights."""
    from app.logs.associate import associate_file_to_all

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        ev_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        await associate_file_to_all(session, file_id)
        await session.commit()

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == ev_ids[0]))
        ).scalar_one()

    assert ev.fight_id == fight_id


# ---------------------------------------------------------------------------
# 7. Decoupled order A: log uploaded AFTER BR ingested
# ---------------------------------------------------------------------------


async def test_order_a_log_after_br(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Order A: BR ingested first, log uploaded later.

    associate_file_to_all (called after upload) must pick up the pre-existing fight.
    """
    from app.logs.associate import associate_file_to_all

    # Step 1: ingest BR (fight exists)
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    # Step 2: log uploaded after BR (no fight at upload time → still picked up later)
    async with db_session_maker() as session:
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        ev_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        # Simulate what the API does: call associate_file_to_all after upload
        await associate_file_to_all(session, file_id)
        await session.commit()

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == ev_ids[0]))
        ).scalar_one()

    assert ev.fight_id == fight_id, "Log uploaded after BR must be associated via file_to_all"


# ---------------------------------------------------------------------------
# 8. Decoupled order B: log uploaded BEFORE BR ingested
# ---------------------------------------------------------------------------


async def test_order_b_log_before_br(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Order B: Log uploaded first (no fights yet), BR ingested later.

    associate_logs_for_br (called after aggregate_br) must pick up the pre-existing file.
    """
    from app.logs.associate import associate_logs_for_br

    # Step 1: log uploaded before any BR/fight exists
    async with db_session_maker() as session:
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        ev_ids = await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        await session.commit()

    # Step 2: BR ingested (fight inserted, then associate_logs_for_br called)
    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.flush()
        # Simulate what the pipeline does: call associate_logs_for_br after aggregate_br
        await associate_logs_for_br(session, br_id)
        await session.commit()

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == ev_ids[0]))
        ).scalar_one()

    assert ev.fight_id == fight_id, "Log uploaded before BR must be associated via logs_for_br"


# ---------------------------------------------------------------------------
# 9. Coverage: missing and covered states
# ---------------------------------------------------------------------------


async def test_coverage_missing_when_no_log(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A user with a participating character but NO log → fights_missing is non-empty."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    # Build a synthetic roster snapshot: USER_A → CHAR_A
    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_A,
                main_character_id=CHAR_A,
                characters=[RosterCharacter(character_id=CHAR_A, character_name="CharA")],
            )
        ],
        version=1,
        fetched_at=0.0,
    )

    reset_roster_store_for_tests()
    settings = get_settings()

    # Patch the roster store in coverage module's namespace
    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    assert len(coverage) == 1
    uc = coverage[0]
    assert uc.user_name == USER_A
    assert len(uc.characters) == 1
    cc = uc.characters[0]
    assert cc.character_id == CHAR_A
    assert fight_id in cc.participated_fights
    assert fight_id in cc.fights_missing
    assert fight_id not in cc.fights_covered
    assert cc.covered is False


async def test_coverage_covered_after_association(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """After uploading + associating a log, the character/fight is marked covered."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.associate import associate_file
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_A,
                main_character_id=CHAR_A,
                characters=[RosterCharacter(character_id=CHAR_A, character_name="CharA")],
            )
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        file_id = await _insert_gamelog_file(session, character_id=CHAR_A)
        await _insert_log_events(session, file_id, CHAR_A, [TS_INSIDE])
        await associate_file(session, file_id)
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    assert len(coverage) == 1
    cc = coverage[0].characters[0]
    assert fight_id in cc.fights_covered
    assert fight_id not in cc.fights_missing
    assert cc.covered is True


async def test_coverage_non_participant_not_listed(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A user whose character did NOT participate in any fight is not listed."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_C,
                main_character_id=CHAR_C,
                characters=[RosterCharacter(character_id=CHAR_C, character_name="CharC")],
            )
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    # CHAR_C did not participate → user not in coverage
    assert coverage == []


async def test_my_coverage_filters_to_user(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """my_coverage returns only the requesting user's entries."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.coverage import my_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_A,
                main_character_id=CHAR_A,
                characters=[RosterCharacter(character_id=CHAR_A, character_name="CharA")],
            ),
            RosterUser(
                user_name=USER_B,
                main_character_id=CHAR_B,
                characters=[RosterCharacter(character_id=CHAR_B, character_name="CharB")],
            ),
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session, victim_char_id=CHAR_A, attacker_char_id=CHAR_B)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    async with db_session_maker() as session:
        cov_a = await my_coverage(session, settings, br_id, USER_A)
        cov_c = await my_coverage(session, settings, br_id, USER_C)

    assert cov_a is not None
    assert cov_a.user_name == USER_A
    assert all(cc.character_id == CHAR_A for cc in cov_a.characters)
    # USER_C has no participating characters
    assert cov_c is None


# ---------------------------------------------------------------------------
# 10. API: coverage endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def _roster_with_char_a(monkeypatch):  # type: ignore[no-untyped-def]
    """Patch the roster store so it returns CHAR_A → USER_A mapping."""
    import app.logs.coverage as coverage_module
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_A,
                main_character_id=CHAR_A,
                characters=[RosterCharacter(character_id=CHAR_A, character_name="CharA")],
            )
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())
    return snap


from tests.conftest import TEST_TOKEN  # noqa: E402


def test_api_coverage_404_unknown_br(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{unknown}/coverage returns 404."""
    client = make_client(DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"))
    resp = client.get(
        "/api/brs/no-such-br/coverage",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert resp.status_code == 404


def test_api_my_coverage_404_unknown_br(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{unknown}/my-coverage returns 404."""
    client = make_client(DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"))
    resp = client.get(
        "/api/brs/no-such-br/my-coverage",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_coverage_returns_matrix(tmp_path, monkeypatch, _roster_with_char_a) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/coverage returns the expected user/character matrix."""
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/coverage", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # USER_A (CHAR_A) participated
    user_entries = {uc["user_name"]: uc for uc in data}
    assert USER_A in user_entries
    chars = user_entries[USER_A]["characters"]
    assert any(c["character_id"] == CHAR_A for c in chars)

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_my_coverage_returns_user_entry(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch, _roster_with_char_a
) -> None:
    """GET /api/brs/{br_id}/my-coverage returns the caller's own entry."""
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    # Request as USER_A (CHAR_A participated)
    hdrs = {
        "Authorization": f"Bearer {TEST_TOKEN}",
        "X-User-Name": USER_A,
        "X-User-Rank": "Member",
        "X-User-Teams": "",
        "X-User-Main-Character-Id": str(CHAR_A),
    }
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/my-coverage", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_name"] == USER_A

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_api_my_coverage_404_no_participating_chars(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch, _roster_with_char_a
) -> None:
    """GET /api/brs/{br_id}/my-coverage returns 404 when user has no participating chars."""
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        fight_id = await _insert_fight(session)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    # Request as a completely different user whose char is NOT in the fight
    hdrs = {
        "Authorization": f"Bearer {TEST_TOKEN}",
        "X-User-Name": "OtherUser",
        "X-User-Rank": "Member",
        "X-User-Teams": "",
        "X-User-Main-Character-Id": "0",
    }
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/my-coverage", headers=hdrs)

    # OtherUser has no participating characters → 404
    assert resp.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 11. associate_logs_for_br: per-file guard
# ---------------------------------------------------------------------------


async def test_associate_logs_for_br_continues_after_one_file_fails(  # type: ignore[no-untyped-def]
    db_session_maker, monkeypatch
) -> None:
    """One failing file must not abort association of the remaining file.

    Two files for CHAR_A are registered.  associate_file is patched so that
    calls for file_id_bad raise, while calls for file_id_good succeed.
    After associate_logs_for_br the good file's events are stamped; no
    exception propagates from the function.
    """
    import app.logs.associate as associate_module
    from app.logs.associate import associate_logs_for_br

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session)

        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))

        # Two files for the same participant character
        file_id_bad = await _insert_gamelog_file(session, character_id=CHAR_A)
        file_id_good = await _insert_gamelog_file(session, character_id=CHAR_A)
        ev_ids = await _insert_log_events(session, file_id_good, CHAR_A, [TS_INSIDE])
        await session.commit()

    # Monkeypatch associate_file so the bad file raises, good file delegates to original
    original_associate_file = associate_module.associate_file

    async def _patched_associate_file(session, file_id, pad_seconds=120):  # type: ignore[no-untyped-def]
        if file_id == file_id_bad:
            raise RuntimeError("simulated parse error for bad file")
        return await original_associate_file(session, file_id, pad_seconds=pad_seconds)

    monkeypatch.setattr(associate_module, "associate_file", _patched_associate_file)

    # Must not raise even though file_id_bad fails
    async with db_session_maker() as session:
        await associate_logs_for_br(session, br_id)
        await session.commit()

    # The good file's events must still be stamped
    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == ev_ids[0]))
        ).scalar_one()

    assert ev.fight_id == fight_id, "Good file events should be stamped despite bad file failing"


# ---------------------------------------------------------------------------
# 12. my_coverage does not scan the full matrix
# ---------------------------------------------------------------------------


async def test_my_coverage_matches_full_matrix_entry(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """my_coverage returns only the caller's entry and it matches the full matrix.

    Two users (USER_A → CHAR_A, USER_B → CHAR_B) both participate.  my_coverage
    for USER_A must return only USER_A's entry and it must equal the corresponding
    entry from the full br_coverage matrix.
    """
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.coverage import br_coverage, my_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_A,
                main_character_id=CHAR_A,
                characters=[RosterCharacter(character_id=CHAR_A, character_name="CharA")],
            ),
            RosterUser(
                user_name=USER_B,
                main_character_id=CHAR_B,
                characters=[RosterCharacter(character_id=CHAR_B, character_name="CharB")],
            ),
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        fight_id = await _insert_fight(session, victim_char_id=CHAR_A, attacker_char_id=CHAR_B)
        br_id = str(uuid.uuid4())
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="ref",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    async with db_session_maker() as session:
        mine = await my_coverage(session, settings, br_id, USER_A)
        full = await br_coverage(session, settings, br_id)

    # my_coverage returns only USER_A
    assert mine is not None
    assert mine.user_name == USER_A
    # Exactly the caller — no USER_B bleed-through
    assert all(cc.character_id == CHAR_A for cc in mine.characters)

    # Entry matches the full matrix
    full_user_a = next((uc for uc in full if uc.user_name == USER_A), None)
    assert full_user_a is not None
    assert mine.characters == full_user_a.characters


# ---------------------------------------------------------------------------
# Regression: bucket flooring must treat naive (SQLite-read) ts as UTC, not
# as the server's local timezone, or buckets shift when TZ != UTC (e.g. BST).
# ---------------------------------------------------------------------------


def test_floor_to_bucket_treats_naive_as_utc_under_nonutc_tz(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time as _time

    from app.logs.associate import _floor_to_bucket

    monkeypatch.setenv("TZ", "Europe/London")  # BST in June = UTC+1
    _time.tzset()
    try:
        naive = dt.datetime(2026, 6, 14, 20, 23, 33)  # UTC wall-clock, no tzinfo
        aware = dt.datetime(2026, 6, 14, 20, 23, 33, tzinfo=dt.UTC)
        expected = dt.datetime(2026, 6, 14, 20, 23, 30, tzinfo=dt.UTC)
        # Naive must be treated as UTC (not shifted into local BST).
        assert _floor_to_bucket(naive) == expected
        # Naive and aware must agree.
        assert _floor_to_bucket(naive) == _floor_to_bucket(aware)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()
