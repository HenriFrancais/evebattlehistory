"""E1: Log-only participants (characters not on any killmail).

TDD tests written BEFORE the implementation.

Scenario
--------
Two characters logged during a fight:
  - CHAR_CONFIRMED (2100000001): victim on a killmail in fight_1.
  - CHAR_LOGI (3300000001): NOT on any killmail, but logs uploaded that overlap fight_1.

fight_1: started_at=2026-06-10 20:00 UTC, ended_at=2026-06-10 20:30 UTC

Additional negative: CHAR_OUTSIDE (3399999999) uploads a log that does NOT overlap
the fight window (way outside the time range) → must NOT be associated.

Roster:
  - USER_CONFIRMED → CHAR_CONFIRMED (killmail participant)
  - USER_LOGI      → CHAR_LOGI      (log-only participant)
  - USER_ABSENT    → CHAR_ABSENT    (on roster, no participation at all)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
import uuid

import pytest
from sqlalchemy import select

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
    SolarSystem,
)

# Character IDs
CHAR_CONFIRMED = 2100000001  # killmail participant (victim)
CHAR_ATTACKER = 2200000001   # killmail participant (attacker)
CHAR_LOGI = 3300000001       # log-only, NOT on any killmail
CHAR_OUTSIDE = 3399999999    # log outside fight window → must NOT be associated
CHAR_ABSENT = 3388888888     # roster member, no participation and no logs

# Roster user names
USER_CONFIRMED = "UserConfirmed"
USER_LOGI = "UserLogi"
USER_ABSENT = "UserAbsent"

# Fight window
FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC)

# Log windows
LOG_START_OVERLAP = dt.datetime(2026, 6, 10, 19, 55, 0, tzinfo=dt.UTC)
LOG_END_OVERLAP = dt.datetime(2026, 6, 10, 20, 35, 0, tzinfo=dt.UTC)

# Log window that does NOT overlap the fight (way before)
LOG_START_NO_OVERLAP = dt.datetime(2026, 6, 10, 15, 0, 0, tzinfo=dt.UTC)
LOG_END_NO_OVERLAP = dt.datetime(2026, 6, 10, 16, 0, 0, tzinfo=dt.UTC)

# Event timestamps
TS_INSIDE = dt.datetime(2026, 6, 10, 20, 15, 0, tzinfo=dt.UTC)
TS_OUTSIDE_WINDOW = dt.datetime(2026, 6, 10, 15, 30, 0, tzinfo=dt.UTC)

_SHIP_TYPE_ID = 1


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_association.py helpers)
# ---------------------------------------------------------------------------


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


async def _insert_character(session, character_id: int, name: str | None = None) -> None:  # type: ignore[no-untyped-def]
    result = await session.execute(select(Character).where(Character.character_id == character_id))
    if result.scalar_one_or_none() is None:
        session.add(
            Character(
                character_id=character_id,
                name=name or f"Char{character_id}",
                last_seen_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.flush()


async def _insert_fight_with_killmail(  # type: ignore[no-untyped-def]
    session,
    system_id: int = 31002222,
    started_at: dt.datetime = FIGHT_START,
    ended_at: dt.datetime = FIGHT_END,
    victim_char_id: int = CHAR_CONFIRMED,
    attacker_char_id: int = CHAR_ATTACKER,
) -> tuple[int, str]:
    """Insert Fight + Killmail + FightKill + BrFight.  Returns (fight_id, br_id)."""
    await _ensure_inventory_type(session)
    await _insert_solar_system(session, system_id)
    await _insert_character(session, victim_char_id, "ConfirmedChar")
    await _insert_character(session, attacker_char_id, "AttackerChar")

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

    session.add(
        KillmailAttacker(
            killmail_id=km_id,
            attacker_idx=0,
            character_id=attacker_char_id,
            damage_done=100,
            final_blow=True,
        )
    )

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

    session.add(FightKill(fight_id=fight.fight_id, killmail_id=km_id, side_idx=0))

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
    session.add(BrFight(br_id=br_id, fight_id=fight.fight_id, seq=0))
    await session.flush()

    return fight.fight_id, br_id


async def _insert_gamelog_file(  # type: ignore[no-untyped-def]
    session,
    character_id: int,
    log_start: dt.datetime = LOG_START_OVERLAP,
    log_end: dt.datetime = LOG_END_OVERLAP,
    parse_status: str = "parsed",
    uploaded_by_user: str = "testuser",
) -> int:
    sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    gf = GamelogFile(
        uploaded_by_user=uploaded_by_user,
        claimed_character_id=character_id,
        listener_name=None,
        character_name=f"Char{character_id}",
        original_filename="test.txt",
        resolved_via="filename",
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
    character_id: int,
    timestamps: list[dt.datetime],
    effect_type: str = "rep",
    direction: str = "out",
    amount: float = 500.0,
) -> list[int]:
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
# 1. associate_file: logi (non-killmail) gets events stamped by time-overlap alone
# ---------------------------------------------------------------------------


async def test_logi_events_stamped_without_killmail_participation(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """CORE: log-only character gets fight_id stamped purely by time-window overlap.

    RED → must fail before the participation filter is removed.
    """
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id, _br_id = await _insert_fight_with_killmail(session)
        # Logi: NOT on any killmail; log overlaps the fight window
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        logi_ev_ids = await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        stamped = await associate_file(session, logi_file_id)
        await session.commit()

    assert stamped == 1, "Logi with time-overlap should have 1 event stamped"

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == logi_ev_ids[0]))
        ).scalar_one()
    assert ev.fight_id == fight_id, "Logi event must get the fight_id"


# ---------------------------------------------------------------------------
# 2. Negative: log with NO time-overlap must NOT be associated
# ---------------------------------------------------------------------------


async def test_no_overlap_log_not_associated(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Log whose timestamps don't overlap any fight window → no fight_id stamped.

    This is the guardrail: time-overlap is the rule; outside the window = no association.
    """
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        _fight_id, _br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_OUTSIDE, "OutsideChar")
        outside_file_id = await _insert_gamelog_file(
            session,
            character_id=CHAR_OUTSIDE,
            log_start=LOG_START_NO_OVERLAP,
            log_end=LOG_END_NO_OVERLAP,
        )
        outside_ev_ids = await _insert_log_events(
            session, outside_file_id, CHAR_OUTSIDE, [TS_OUTSIDE_WINDOW]
        )
        await session.commit()

    async with db_session_maker() as session:
        stamped = await associate_file(session, outside_file_id)
        await session.commit()

    assert stamped == 0, "Log outside fight window must not be associated"

    async with db_session_maker() as session:
        ev = (
            await session.execute(
                select(LogEvent).where(LogEvent.event_id == outside_ev_ids[0])
            )
        ).scalar_one()
    assert ev.fight_id is None, "Event outside window must have no fight_id"


# ---------------------------------------------------------------------------
# 3. associate_logs_for_br: picks up logi's file (time-overlap only)
# ---------------------------------------------------------------------------


async def test_associate_logs_for_br_includes_logi(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """associate_logs_for_br must pick up logi's file even though logi not on killmail."""
    from app.logs.associate import associate_logs_for_br

    async with db_session_maker() as session:
        fight_id, br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        logi_ev_ids = await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        await associate_logs_for_br(session, br_id)
        await session.commit()

    async with db_session_maker() as session:
        ev = (
            await session.execute(select(LogEvent).where(LogEvent.event_id == logi_ev_ids[0]))
        ).scalar_one()
    assert ev.fight_id == fight_id


# ---------------------------------------------------------------------------
# 4. br_logged_char_ids: includes logi
# ---------------------------------------------------------------------------


async def test_br_logged_char_ids_includes_logi(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """br_logged_char_ids returns logi's char_id (log-only character with fight_id stamped)."""
    from app.fights.participants import br_logged_char_ids
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        _fight_id, br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await associate_file(session, logi_file_id)
        await session.commit()

    async with db_session_maker() as session:
        logged = await br_logged_char_ids(session, br_id)

    assert CHAR_LOGI in logged
    # CHAR_CONFIRMED is NOT logged (no gamelog file for them in this test)
    assert CHAR_CONFIRMED not in logged


# ---------------------------------------------------------------------------
# 5. br_participants: union of killmail + logged characters with flags
# ---------------------------------------------------------------------------


async def test_br_participants_union_with_flags(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """br_participants returns CONFIRMED and LOGI, each with correct flags.

    - CHAR_CONFIRMED: on_killmail=True, has_logs=False (no log uploaded)
    - CHAR_LOGI:      on_killmail=False, has_logs=True
    """
    from app.config import get_settings
    from app.fights.participants import br_participants
    from app.logs.associate import associate_file
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_CONFIRMED,
                main_character_id=CHAR_CONFIRMED,
                characters=[
                    RosterCharacter(character_id=CHAR_CONFIRMED, character_name="ConfirmedChar")
                ],
            ),
            RosterUser(
                user_name=USER_LOGI,
                main_character_id=CHAR_LOGI,
                characters=[RosterCharacter(character_id=CHAR_LOGI, character_name="LogiChar")],
            ),
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    async with db_session_maker() as session:
        fight_id, br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await associate_file(session, logi_file_id)
        await session.commit()

    from unittest.mock import patch

    import app.fights.participants as participants_module

    # Patch roster so br_participants can resolve user names
    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    with patch.object(participants_module, "get_roster_store", return_value=_FakeStore()):
        async with db_session_maker() as session:
            participants = await br_participants(session, settings, br_id)

    by_char = {p.character_id: p for p in participants}

    # CHAR_CONFIRMED: on killmail, no logs uploaded
    assert CHAR_CONFIRMED in by_char
    confirmed = by_char[CHAR_CONFIRMED]
    assert confirmed.on_killmail is True
    assert confirmed.has_logs is False

    # CHAR_LOGI: not on killmail, has logs
    assert CHAR_LOGI in by_char
    logi = by_char[CHAR_LOGI]
    assert logi.on_killmail is False
    assert logi.has_logs is True

    # fight_ids populated
    assert fight_id in confirmed.fight_ids
    assert fight_id in logi.fight_ids


# ---------------------------------------------------------------------------
# 6. Coverage: logi appears as covered (has_logs=True, on_killmail=False)
# ---------------------------------------------------------------------------


async def test_coverage_logi_appears_covered(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Logi with logs (no killmail) appears in coverage with on_killmail=False, covered=True."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.associate import associate_file
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_LOGI,
                main_character_id=CHAR_LOGI,
                characters=[RosterCharacter(character_id=CHAR_LOGI, character_name="LogiChar")],
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
        fight_id, br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await associate_file(session, logi_file_id)
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    assert len(coverage) == 1
    uc = coverage[0]
    assert uc.user_name == USER_LOGI
    assert len(uc.characters) == 1
    cc = uc.characters[0]
    assert cc.character_id == CHAR_LOGI
    assert cc.on_killmail is False  # new flag
    assert cc.has_logs is True      # new flag
    assert cc.covered is True       # logi uploaded logs → covered
    assert fight_id in cc.fights_covered


# ---------------------------------------------------------------------------
# 7. Coverage: killmail participant with no logs → still missing
# ---------------------------------------------------------------------------


async def test_coverage_killmail_participant_no_logs_still_missing(
    db_session_maker, monkeypatch
) -> None:
    """Killmail participant who uploaded NO logs still shows as missing.

    on_killmail=True, has_logs=False, covered=False.
    """
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_CONFIRMED,
                main_character_id=CHAR_CONFIRMED,
                characters=[
                    RosterCharacter(character_id=CHAR_CONFIRMED, character_name="ConfirmedChar")
                ],
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
        fight_id, br_id = await _insert_fight_with_killmail(session)
        # No log uploaded for CHAR_CONFIRMED
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    assert len(coverage) == 1
    uc = coverage[0]
    cc = uc.characters[0]
    assert cc.character_id == CHAR_CONFIRMED
    assert cc.on_killmail is True
    assert cc.has_logs is False
    assert cc.covered is False
    assert fight_id in cc.fights_missing


# ---------------------------------------------------------------------------
# 8. Idempotency: re-associating logi's log produces stable result
# ---------------------------------------------------------------------------


async def test_logi_association_idempotent(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Running associate_file twice for logi yields same stamped count, no dup buckets."""
    from sqlalchemy import func

    from app.db.models import LogEventBucket
    from app.logs.associate import associate_file

    async with db_session_maker() as session:
        fight_id, _br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE, TS_INSIDE])
        await session.commit()

    async with db_session_maker() as session:
        count1 = await associate_file(session, logi_file_id)
        await session.commit()

    async with db_session_maker() as session:
        count2 = await associate_file(session, logi_file_id)
        await session.commit()

    assert count1 == count2

    async with db_session_maker() as session:
        bucket_count = (
            await session.execute(
                select(func.count())
                .select_from(LogEventBucket)
                .where(LogEventBucket.fight_id == fight_id)
                .where(LogEventBucket.character_id == CHAR_LOGI)
            )
        ).scalar()

    assert bucket_count == 1, "Idempotent: exactly one bucket, no duplicates"


# ---------------------------------------------------------------------------
# 9. API: GET /api/brs/{br_id}/participants returns participants with flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_br_participants_endpoint(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/participants returns the union list with on_killmail/has_logs."""
    from fastapi.testclient import TestClient

    import app.fights.participants as participants_module
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.logs.associate import associate_file
    from app.main import create_app
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests
    from tests.conftest import TEST_TOKEN

    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name=USER_CONFIRMED,
                main_character_id=CHAR_CONFIRMED,
                characters=[
                    RosterCharacter(character_id=CHAR_CONFIRMED, character_name="ConfirmedChar")
                ],
            ),
            RosterUser(
                user_name=USER_LOGI,
                main_character_id=CHAR_LOGI,
                characters=[RosterCharacter(character_id=CHAR_LOGI, character_name="LogiChar")],
            ),
        ],
        version=1,
        fetched_at=0.0,
    )
    reset_roster_store_for_tests()

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
        _fight_id, br_id = await _insert_fight_with_killmail(session)
        await _insert_character(session, CHAR_LOGI, "LogiChar")
        logi_file_id = await _insert_gamelog_file(session, character_id=CHAR_LOGI)
        await _insert_log_events(session, logi_file_id, CHAR_LOGI, [TS_INSIDE])
        await associate_file(session, logi_file_id)
        await session.commit()

    get_app_config.cache_clear()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(participants_module, "get_roster_store", lambda s: _FakeStore())

    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get(f"/api/brs/{br_id}/participants", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 2

    by_char = {p["character_id"]: p for p in data}
    assert CHAR_CONFIRMED in by_char
    assert by_char[CHAR_CONFIRMED]["on_killmail"] is True

    assert CHAR_LOGI in by_char
    assert by_char[CHAR_LOGI]["on_killmail"] is False
    assert by_char[CHAR_LOGI]["has_logs"] is True

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 10. API: GET /api/brs/{br_id}/participants returns 404 for unknown BR
# ---------------------------------------------------------------------------


def test_api_br_participants_404(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{unknown}/participants returns 404."""
    from tests.conftest import TEST_TOKEN

    client = make_client(DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"))
    resp = client.get(
        "/api/brs/no-such-br/participants",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Coverage list ordering (Part 2): by user, then character, alphabetically.
# ---------------------------------------------------------------------------


async def test_br_coverage_sorted_by_user_then_character(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """br_coverage returns users alphabetically, characters alphabetical within each user."""
    import app.logs.coverage as coverage_module
    from app.config import get_settings
    from app.logs.associate import associate_file
    from app.logs.coverage import br_coverage
    from app.roster.models import RosterCharacter, RosterUser
    from app.roster.snapshot import build_roster_snapshot, reset_roster_store_for_tests

    # Two users out of order; characters within each user out of order.
    Z_YARA, Z_ABE, A_TOM, A_BO = 4100000001, 4100000002, 4100000003, 4100000004
    snap = build_roster_snapshot(
        users=[
            RosterUser(
                user_name="Zeb", main_character_id=Z_YARA,
                characters=[
                    RosterCharacter(character_id=Z_YARA, character_name="Yara"),
                    RosterCharacter(character_id=Z_ABE, character_name="Abe"),
                ],
            ),
            RosterUser(
                user_name="Amy", main_character_id=A_TOM,
                characters=[
                    RosterCharacter(character_id=A_TOM, character_name="Tom"),
                    RosterCharacter(character_id=A_BO, character_name="Bo"),
                ],
            ),
        ],
        version=1, fetched_at=0.0,
    )
    reset_roster_store_for_tests()
    settings = get_settings()

    class _FakeStore:
        async def get(self):  # type: ignore[no-untyped-def]
            return snap

    monkeypatch.setattr(coverage_module, "get_roster_store", lambda s: _FakeStore())

    async with db_session_maker() as session:
        _fight_id, br_id = await _insert_fight_with_killmail(session)
        for cid, name in ((Z_YARA, "Yara"), (Z_ABE, "Abe"), (A_TOM, "Tom"), (A_BO, "Bo")):
            await _insert_character(session, cid, name)
            fid = await _insert_gamelog_file(session, character_id=cid)
            await _insert_log_events(session, fid, cid, [TS_INSIDE])
            await associate_file(session, fid)
        await session.commit()

    async with db_session_maker() as session:
        coverage = await br_coverage(session, settings, br_id)

    assert [u.user_name for u in coverage] == ["Amy", "Zeb"]
    amy = next(u for u in coverage if u.user_name == "Amy")
    zeb = next(u for u in coverage if u.user_name == "Zeb")
    assert [c.character_name for c in amy.characters] == ["Bo", "Tom"]
    assert [c.character_name for c in zeb.characters] == ["Abe", "Yara"]
