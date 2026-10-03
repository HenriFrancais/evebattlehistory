"""Strict privacy policy: per-pilot log-derived data and the user↔character
mapping are FC/HC-only. A non-elevated member sees fleet aggregates plus rows
about their OWN characters; nothing that names another pilot's log data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN
from tests.test_association import _insert_fight, _insert_gamelog_file, _insert_log_events

RAZOK_CHAR = 2112615087  # owned by Ra'zok (High Command)
MEMBER_CHAR = 95000001  # owned by LineMember (Member)

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0)
T0 = int(FIGHT_START.replace(tzinfo=dt.UTC).timestamp())
T1 = int(FIGHT_END.replace(tzinfo=dt.UTC).timestamp())


@pytest.fixture
async def privacy_br(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A BR with one fight where BOTH roster characters uploaded logs."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport, BrFight, Broadcast, BroadcastFile, LogEvent
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DEV_MODE", "0")
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()
    settings = get_settings()
    await init_models(settings)

    async with get_sessionmaker(settings)() as session:
        fight_id = await _insert_fight(
            session,
            victim_char_id=MEMBER_CHAR,
            attacker_char_id=RAZOK_CHAR,
            started_at=FIGHT_START,
            ended_at=FIGHT_END,
        )
        br_id = str(uuid.uuid4())
        session.add(BattleReport(
            br_id=br_id, source="demo", source_url="http://x", source_ref="ref",
            created_by_user="test", status="ready", progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        ))
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        ts = [FIGHT_START + dt.timedelta(seconds=30)]
        for cid in (MEMBER_CHAR, RAZOK_CHAR):
            file_id = await _insert_gamelog_file(
                session, cid, log_start=FIGHT_START, log_end=FIGHT_END
            )
            await _insert_log_events(session, file_id, cid, ts, "damage", "out", 500.0)
            await _insert_log_events(session, file_id, cid, ts, "rep_armor", "out", 300.0)
            await _insert_log_events(session, file_id, cid, ts, "neut", "out", 40.0)
        await session.execute(update(LogEvent).values(fight_id=fight_id, other_name="Enemy"))

        bf = BroadcastFile(
            br_id=br_id, uploaded_by_user="Ra'zok", original_filename="b.txt",
            stored_path="/tmp/b.txt", sha256=hashlib.sha256(b"b").hexdigest(),
            mime="text/plain", size=1, parse_status="parsed", broadcast_count=3,
            superseded=False, uploaded_at=dt.datetime.now(dt.UTC),
        )
        session.add(bf)
        await session.flush()
        for seq, (kind, name, cid) in enumerate([
            ("needs_armor", "Ra'zok Zateki", RAZOK_CHAR),
            ("needs_armor", "Line Member", MEMBER_CHAR),
            ("target", "Some Enemy", None),
        ]):
            session.add(Broadcast(
                file_id=bf.broadcast_file_id, br_id=br_id, fight_id=fight_id, ts=ts[0],
                kind=kind, subject_name=name, subject_ship="Guardian",
                subject_character_id=cid, seq=seq, raw_line="x",
            ))
        await session.commit()

    with TestClient(create_app()) as client:
        yield client, br_id, fight_id

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


def _source_ids(rows: list[dict]) -> set[int | None]:  # type: ignore[type-arg]
    return {r["source_character_id"] for r in rows}


async def test_snapshot_member_sees_only_own_characters(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/snapshot?from_ts={T0}&to_ts={T1}"
    member = client.get(url, headers=MEMBER_HEADERS)
    assert member.status_code == 200, member.text
    ids = _source_ids(member.json()["rows"])
    assert MEMBER_CHAR in ids
    assert RAZOK_CHAR not in ids
    # The response says it is restricted, so the UI can explain the smaller view.
    assert member.json()["scope"] == "own"


async def test_snapshot_elevated_sees_everyone(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/snapshot?from_ts={T0}&to_ts={T1}"
    body = client.get(url, headers=CREATOR_HEADERS).json()
    assert {MEMBER_CHAR, RAZOK_CHAR} <= _source_ids(body["rows"])
    assert body["scope"] == "all"


async def test_reconcile_member_sees_only_own_rows(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, fight_id = privacy_br
    url = f"/api/brs/{br_id}/fights/{fight_id}/reconcile"
    body = client.get(url, headers=MEMBER_HEADERS).json()
    assert {r["character_id"] for r in body["rows"]} == {MEMBER_CHAR}

    elevated = client.get(url, headers=CREATOR_HEADERS).json()
    assert {r["character_id"] for r in elevated["rows"]} == {MEMBER_CHAR, RAZOK_CHAR}


async def test_ewar_member_sees_only_own_rows(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, fight_id = privacy_br
    url = f"/api/brs/{br_id}/fights/{fight_id}/ewar"
    body = client.get(url, headers=MEMBER_HEADERS).json()
    assert {r["character_id"] for r in body["logi"]} == {MEMBER_CHAR}
    assert {r["character_id"] for r in body["cap"]} == {MEMBER_CHAR}

    elevated = client.get(url, headers=CREATOR_HEADERS).json()
    assert {r["character_id"] for r in elevated["logi"]} == {MEMBER_CHAR, RAZOK_CHAR}


async def test_coverage_matrix_is_elevated_only(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/coverage"
    assert client.get(url, headers=MEMBER_HEADERS).status_code == 403
    assert client.get(url, headers=CREATOR_HEADERS).status_code == 200


async def test_participants_hide_other_users_from_member(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/participants"
    rows = {r["character_id"]: r for r in client.get(url, headers=MEMBER_HEADERS).json()}
    assert rows[MEMBER_CHAR]["user_name"] == "LineMember"
    assert rows[MEMBER_CHAR]["has_logs"] is True
    assert rows[RAZOK_CHAR]["user_name"] is None
    assert rows[RAZOK_CHAR]["has_logs"] is False

    elevated = {r["character_id"]: r for r in client.get(url, headers=CREATOR_HEADERS).json()}
    assert elevated[RAZOK_CHAR]["user_name"] == "Ra'zok"
    assert elevated[RAZOK_CHAR]["has_logs"] is True


async def test_raw_broadcasts_hide_other_pilots_rep_requests(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/broadcasts"
    names = {r["subject_name"] for r in client.get(url, headers=MEMBER_HEADERS).json()}
    assert names == {"Line Member", "Some Enemy"}

    elevated = {r["subject_name"] for r in client.get(url, headers=CREATOR_HEADERS).json()}
    assert elevated == {"Line Member", "Some Enemy", "Ra'zok Zateki"}


async def test_composition_hides_other_pilots_log_fields_from_member(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, br_id, _ = privacy_br
    url = f"/api/brs/{br_id}/composition"

    def pilots(headers: dict[str, str]) -> dict[int, dict]:  # type: ignore[type-arg]
        body = client.get(url, headers=headers).json()
        return {p["character_id"]: p for s in body["sides"] for p in s["pilots"]}

    elevated = pilots(CREATOR_HEADERS)
    assert elevated[RAZOK_CHAR]["has_logs"] is True
    assert elevated[RAZOK_CHAR]["reps_out"] > 0

    member = pilots(MEMBER_HEADERS)
    assert member[RAZOK_CHAR]["has_logs"] is False
    assert member[RAZOK_CHAR]["reps_out"] == 0
    assert member[MEMBER_CHAR]["has_logs"] is True
    assert member[MEMBER_CHAR]["reps_out"] > 0


async def test_roster_users_is_dev_mode_only(privacy_br) -> None:  # type: ignore[no-untyped-def]
    client, _, _ = privacy_br
    assert client.get("/api/roster/users", headers=MEMBER_HEADERS).status_code == 404
    assert client.get("/api/roster/users", headers=CREATOR_HEADERS).status_code == 404


def test_friendly_leaders_are_redacted_for_other_pilots() -> None:
    from app.analytics.fleet import LeaderEntry, Leaders
    from app.api.fleet import _leaders_out

    mine = LeaderEntry(name="Line Member", ship="Guardian", amount=5.0, character_id=MEMBER_CHAR)
    theirs = LeaderEntry(name="Ra'zok Zateki", ship="Loki", amount=9.0, character_id=RAZOK_CHAR)
    enemy = LeaderEntry(name="Some Enemy", ship="Loki", amount=7.0, character_id=42)
    ld = Leaders(
        top_friendly_dmg_taken=theirs,
        top_hostile_dmg_taken=enemy,
        top_friendly_rep_recv=mine,
    )

    member = _leaders_out(ld, elevated=False, own_character_ids={MEMBER_CHAR})
    assert member.top_friendly_dmg_taken is None
    assert member.top_friendly_rep_recv is not None
    assert member.top_friendly_rep_recv.name == "Line Member"
    # Enemy pilots are not private — they come from our own logs about them.
    assert member.top_hostile_dmg_taken is not None

    elevated = _leaders_out(ld, elevated=True, own_character_ids=set())
    assert elevated.top_friendly_dmg_taken is not None
    assert elevated.top_friendly_dmg_taken.name == "Ra'zok Zateki"
