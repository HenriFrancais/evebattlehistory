"""Members can remove their own uploads, and a log with no combat in it is
reported as such instead of being stored as an 'unresolved' failure."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS

HEADER = (
    b"------------------------------------------------------------\n"
    b"  Gamelog\n"
    b"  Listener: %s\n"
    b"  Session Started: 2026.01.01 12:00:00\n"
    b"------------------------------------------------------------\n"
)
COMBAT = (
    b"[ 2026.01.01 12:00:02 ] (combat) 432 from Enemy Pilot[TST](Brutix) - 250mm Railgun II - Hits\n"
)
MEMBER_LOG = HEADER % b"Line Member" + COMBAT
MEMBER_NAME = "20260101_120000_95000001.txt"
EMPTY_LOG = HEADER % b"Line Member" + b"[ 2026.01.01 12:00:01 ] (notify) Undocking\n"
NO_LISTENER = (
    b"------------------------------------------------------------\n"
    b"  Gamelog\n"
    b"------------------------------------------------------------\n"
)


def _client(make_client, tmp_path):  # type: ignore[no-untyped-def]
    return make_client(DB_PATH=str(tmp_path / "t.db"), LOG_DIR=str(tmp_path / "logs"))


def _upload(client, raw: bytes, name: str = MEMBER_NAME, headers=MEMBER_HEADERS) -> dict:  # type: ignore[no-untyped-def,type-arg]
    r = client.post("/api/logs", headers=headers, files=[("files", (name, raw, "text/plain"))])
    assert r.status_code == 200, r.text
    return r.json()[0]


def _stored(tmp_path: Path) -> list[Path]:
    d = tmp_path / "logs"
    return sorted(d.iterdir()) if d.exists() else []


def test_log_without_combat_is_reported_empty_and_not_stored(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    for raw in (EMPTY_LOG, NO_LISTENER):
        res = _upload(client, raw)
        assert res["status"] == "empty"
        assert res["file_id"] is None
        assert "no combat" in res["message"].lower()
    assert client.get("/api/logs/mine", headers=MEMBER_HEADERS).json() == []
    assert _stored(tmp_path) == []


def test_member_deletes_own_upload(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    res = _upload(client, MEMBER_LOG)
    assert res["status"] == "parsed"
    assert len(_stored(tmp_path)) == 1

    r = client.delete(f"/api/logs/{res['file_id']}", headers=MEMBER_HEADERS)
    assert r.status_code == 200, r.text
    assert client.get("/api/logs/mine", headers=MEMBER_HEADERS).json() == []
    assert _stored(tmp_path) == []
    with sqlite3.connect(tmp_path / "t.db") as c:
        assert c.execute("select count(*) from log_event").fetchone()[0] == 0
    # The same file can be uploaded again afterwards.
    assert _upload(client, MEMBER_LOG)["status"] == "parsed"


def test_only_the_uploader_or_fc_may_delete(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    theirs = _upload(client, MEMBER_LOG)
    other = {**MEMBER_HEADERS, "X-User-Name": "SomeoneElse", "X-User-Main-Character-Id": "1"}
    assert client.delete(f"/api/logs/{theirs['file_id']}", headers=other).status_code == 403
    assert client.delete("/api/logs/999999", headers=MEMBER_HEADERS).status_code == 404
    assert client.delete(f"/api/logs/{theirs['file_id']}", headers=CREATOR_HEADERS).status_code == 200


def test_deleting_a_log_removes_its_data_from_the_fight(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_client, tmp_path)
    db = tmp_path / "t.db"
    with sqlite3.connect(db) as c:
        c.execute("insert into solar_system (system_id, name) values (1, 'J1')")
        c.execute(
            "insert into fight (fight_id, system_id, started_at, ended_at, isk_destroyed_total,"
            " largest_side_pilots, capitals_involved, distinct_alliance_count)"
            " values (7, 1, '2026-01-01 11:59:00.000000', '2026-01-01 12:05:00.000000', 0, 1, 0, 1)"
        )
    res = _upload(client, MEMBER_LOG)
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from log_event where fight_id = 7").fetchone()[0] == 1
        assert c.execute("select count(*) from log_event_bucket where fight_id = 7").fetchone()[0] == 1

    assert client.delete(f"/api/logs/{res['file_id']}", headers=MEMBER_HEADERS).status_code == 200
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from log_event_bucket where fight_id = 7").fetchone()[0] == 0


async def test_migration_removes_stored_empty_logs(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.engine import init_models, reset_engine_for_tests

    db = tmp_path / "old.db"
    monkeypatch.setenv("DB_PATH", str(db))
    get_settings.cache_clear()
    reset_engine_for_tests()
    await init_models(get_settings())
    reset_engine_for_tests()
    now = dt.datetime(2026, 1, 1).isoformat(sep=" ")
    with sqlite3.connect(db) as c:
        for fid, sha, status in ((1, "a", "unresolved"), (2, "b", "parsed")):
            c.execute(
                "insert into gamelog_file (file_id, uploaded_by_user, resolved_via, stored_path,"
                " sha256, mime, size, parse_status, event_count, combat_lines, unmatched_combat,"
                " uploaded_at) values (?, 'u', 'filename', '/x', ?, 'text/plain', 1, ?, 0, 0, 0, ?)",
                (fid, sha, status, now),
            )
        c.execute("insert into log_event (file_id, ts, effect_type, authoritative,"
                  " dedupe_suppressed) values (2, '2026-01-01 12:00:00', 'damage', 0, 0)")
        c.execute("pragma user_version = 2")
    await init_models(get_settings())
    reset_engine_for_tests()
    with sqlite3.connect(db) as c:
        assert c.execute("select file_id from gamelog_file").fetchall() == [(2,)]
