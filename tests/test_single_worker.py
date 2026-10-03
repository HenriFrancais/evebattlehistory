"""Coordination state (ingest lock, off-BR cache) is per-process, so the app must
run as ONE worker, and writes that change a BR's derived data must drop caches."""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import CREATOR_HEADERS

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "gamelogs"


def test_container_runs_a_single_worker() -> None:
    cmd = (ROOT / "deploy" / "Dockerfile").read_text()
    workers = re.findall(r"gunicorn[^\n]*?-w (\d+)", cmd)
    assert workers == ["1"], "in-process ingest lock + caches require exactly one worker"


def test_log_upload_invalidates_offbr_cache(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.fights.offbr_cache import get_offbr_cache

    client = make_client(DB_PATH=str(tmp_path / "test.db"), LOG_DIR=str(tmp_path / "logs"))
    cache = get_offbr_cache()
    cache._store("some-br", [])
    assert cache._fresh("some-br") is not None

    raw = (FIXTURES / "full_fight.txt").read_bytes()
    resp = client.post(
        "/api/logs",
        files=[("files", ("20260616_192114_2112615087.txt", raw, "text/plain"))],
        headers=CREATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "parsed"
    # A new log can add off-BR participants to any BR whose fights it overlaps.
    assert cache._fresh("some-br") is None
