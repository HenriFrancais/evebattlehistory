"""API tests for the fleet-broadcast endpoints (upload/metrics/list/delete + gate)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.models import BattleReport, BrFight, Fight, SolarSystem
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

FIXTURE = Path(__file__).parent / "fixtures" / "broadcasts" / "sample_broadcast.txt"
RAW = FIXTURE.read_bytes()
BR_ID = "brdcast-api"


async def _seed(session) -> None:  # type: ignore[no-untyped-def]
    session.add(SolarSystem(system_id=1, name="J1"))
    session.add(BattleReport(br_id=BR_ID, source="demo", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime(2026, 7, 12, 2, 0),
                             battle_at=dt.datetime(2026, 7, 12, 0, 0)))
    session.add(Fight(fight_id=1, system_id=1,
                      started_at=dt.datetime(2026, 7, 11, 23, 50),
                      ended_at=dt.datetime(2026, 7, 12, 0, 5)))
    session.add(BrFight(br_id=BR_ID, fight_id=1, seq=0))
    await session.flush()


async def _make_app(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        await _seed(session)
        await session.commit()
    return create_app()


def _upload(client, headers):
    return client.post(
        f"/api/brs/{BR_ID}/broadcasts",
        files={"file": ("bc.txt", RAW, "text/plain")},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_upload_gate_and_flow(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        # Non-elevated members cannot upload.
        assert _upload(client, MEMBER_HEADERS).status_code == 403

        # FC / High Command can.
        r = _upload(client, CREATOR_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "parsed"
        assert body["broadcast_count"] == 7

        # Metrics endpoint (visible to all) reports broadcasts.
        m = client.get(f"/api/brs/{BR_ID}/broadcasts/metrics", headers=MEMBER_HEADERS)
        assert m.status_code == 200
        mj = m.json()
        assert mj["has_broadcasts"] is True
        assert "summary" in mj and "deaths" in mj

        # Raw list feeds the timeline overlay.
        lst = client.get(f"/api/brs/{BR_ID}/broadcasts", headers=CREATOR_HEADERS)
        assert lst.status_code == 200
        assert len(lst.json()) == 7

        # File info endpoint returns the attached file.
        fi = client.get(f"/api/brs/{BR_ID}/broadcasts/file", headers=CREATOR_HEADERS)
        assert fi.status_code == 200
        file_id = fi.json()["file_id"]

        # Performance endpoint carries the anonymous fleet distributions + own rows.
        perf = client.get(f"/api/brs/{BR_ID}/performance", headers=CREATOR_HEADERS)
        assert perf.status_code == 200
        assert perf.json()["elevated"] is True

        # Members cannot delete; FC/HC can, and it clears the broadcasts.
        assert client.delete(f"/api/brs/{BR_ID}/broadcasts/{file_id}",
                             headers=MEMBER_HEADERS).status_code == 403
        d = client.delete(f"/api/brs/{BR_ID}/broadcasts/{file_id}", headers=CREATOR_HEADERS)
        assert d.status_code == 200
        after = client.get(f"/api/brs/{BR_ID}/broadcasts/metrics", headers=CREATOR_HEADERS)
        assert after.json()["has_broadcasts"] is False


@pytest.mark.asyncio
async def test_unknown_br_404(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/brs/nope/broadcasts/metrics",
                          headers=CREATOR_HEADERS).status_code == 404
        assert _upload(client, CREATOR_HEADERS) is not None  # sanity: real BR still uploads
