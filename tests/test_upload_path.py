"""Log upload must not hold the SQLite write lock across ESI calls, must parse
off the event loop, and must bound what it reads into memory."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from tests.conftest import CREATOR_HEADERS

FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"
NAME = "20260616_192114_2112615087.txt"


def _raw() -> bytes:
    return (FIXTURES / "full_fight.txt").read_bytes()


def test_esi_resolution_runs_after_the_upload_is_committed(  # type: ignore[no-untyped-def]
    make_client, tmp_path, monkeypatch
) -> None:
    import app.api.logs as logs_api

    db = tmp_path / "test.db"
    client = make_client(DB_PATH=str(db), LOG_DIR=str(tmp_path / "logs"))
    seen: list[tuple[int, int]] = []

    async def fake_resolve(session, settings, names, esi=None):  # type: ignore[no-untyped-def]
        # A second connection can only see the file if the upload transaction has
        # already COMMITTED — i.e. the write lock is not held during "ESI".
        with sqlite3.connect(db) as c:
            committed = c.execute("select count(*) from gamelog_file").fetchone()[0]
        seen.append((committed, len(names)))
        return 0

    monkeypatch.setattr(logs_api, "resolve_log_characters", fake_resolve)
    resp = client.post(
        "/api/logs", files=[("files", (NAME, _raw(), "text/plain"))], headers=CREATOR_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "parsed"
    assert len(seen) == 1
    committed, n_names = seen[0]
    assert committed == 1
    assert n_names > 0


def test_parsing_runs_off_the_event_loop(make_client, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.logs.ingest as ingest_mod

    client = make_client(DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"))
    real = ingest_mod.parse_log
    threads: list[tuple[int, bool]] = []

    def spy(text: str):  # type: ignore[no-untyped-def]
        import asyncio

        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        threads.append((threading.get_ident(), on_loop))
        return real(text)

    monkeypatch.setattr(ingest_mod, "parse_log", spy)
    resp = client.post(
        "/api/logs", files=[("files", (NAME, _raw(), "text/plain"))], headers=CREATOR_HEADERS
    )
    assert resp.status_code == 200
    assert threads and threads[0][1] is False, "parse_log ran on the event loop"


def test_too_many_files_in_one_request_is_rejected(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"),
        MAX_UPLOAD_FILES="2",
    )
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(3)]
    resp = client.post("/api/logs", files=files, headers=CREATOR_HEADERS)
    assert resp.status_code == 413


def test_oversize_file_is_rejected_without_being_read(  # type: ignore[no-untyped-def]
    make_client, tmp_path, monkeypatch
) -> None:
    from starlette.datastructures import UploadFile

    client = make_client(
        DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"), MAX_LOG_MB="0"
    )
    reads: list[int] = []
    real_read = UploadFile.read

    async def spy_read(self, size: int = -1):  # type: ignore[no-untyped-def]
        reads.append(size)
        return await real_read(self, size)

    monkeypatch.setattr(UploadFile, "read", spy_read)
    resp = client.post(
        "/api/logs", files=[("files", ("big.txt", _raw(), "text/plain"))], headers=CREATOR_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "error"
    assert "too large" in resp.json()[0]["message"].lower()
    assert reads == []
