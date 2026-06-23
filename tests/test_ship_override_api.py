"""API tests for the per-character ship override + ship-type search."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import BattleReport, BrCharShip, InventoryType
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

GUARDIAN = 11987


async def _seed(session) -> str:  # type: ignore[no-untyped-def]
    session.add(InventoryType(type_id=GUARDIAN, name="Guardian", category_id=6))
    session.add(InventoryType(type_id=620, name="Osprey", category_id=6))
    session.add(InventoryType(type_id=999, name="Guristas Tower", category_id=23))  # not a ship
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="demo", source_url="x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime.now(dt.UTC)))
    await session.flush()
    return br_id


@pytest.mark.asyncio
async def test_participant_ship_override_and_search(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        br_id = await _seed(session)
        await session.commit()

    app = create_app()
    with TestClient(app) as client:
        # Member cannot set a ship.
        rm = client.put(f"/api/brs/{br_id}/participants/42/ship", headers=MEMBER_HEADERS,
                        json={"ship_type_id": GUARDIAN})
        assert rm.status_code == 403

        # FC sets it.
        rp = client.put(f"/api/brs/{br_id}/participants/42/ship", headers=CREATOR_HEADERS,
                        json={"ship_type_id": GUARDIAN})
        assert rp.status_code == 200

        # Ship-type search returns only ships matching the query.
        rs = client.get("/api/ship-types?q=guar", headers=CREATOR_HEADERS)
        assert rs.status_code == 200
        names = {s["name"] for s in rs.json()}
        assert "Guardian" in names
        assert "Guristas Tower" not in names  # non-ship category excluded

    async with sm() as session:
        row = (await session.execute(
            select(BrCharShip).where(BrCharShip.br_id == br_id, BrCharShip.character_id == 42)
        )).scalar_one()
        assert row.ship_type_id == GUARDIAN

    # FC clears it.
    with TestClient(app) as client:
        rc = client.put(f"/api/brs/{br_id}/participants/42/ship", headers=CREATOR_HEADERS,
                        json={"ship_type_id": None})
        assert rc.status_code == 200
    async with sm() as session:
        gone = (await session.execute(
            select(BrCharShip).where(BrCharShip.br_id == br_id, BrCharShip.character_id == 42)
        )).scalar_one_or_none()
        assert gone is None

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_participant_side_override(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BrCharSide
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        br_id = await _seed(session)
        await session.commit()

    app = create_app()
    with TestClient(app) as client:
        # Member cannot set a side.
        rm = client.put(f"/api/brs/{br_id}/participants/7/side", headers=MEMBER_HEADERS,
                        json={"side": "friendly"})
        assert rm.status_code == 403
        # Invalid side rejected.
        rb = client.put(f"/api/brs/{br_id}/participants/7/side", headers=CREATOR_HEADERS,
                        json={"side": "banana"})
        assert rb.status_code == 400
        # FC sets it.
        rp = client.put(f"/api/brs/{br_id}/participants/7/side", headers=CREATOR_HEADERS,
                        json={"side": "hostile"})
        assert rp.status_code == 200

    async with sm() as session:
        row = (await session.execute(
            select(BrCharSide).where(BrCharSide.br_id == br_id, BrCharSide.character_id == 7)
        )).scalar_one()
        assert row.side == "hostile"

    with TestClient(app) as client:
        rc = client.put(f"/api/brs/{br_id}/participants/7/side", headers=CREATOR_HEADERS,
                        json={"side": None})
        assert rc.status_code == 200
    async with sm() as session:
        gone = (await session.execute(
            select(BrCharSide).where(BrCharSide.br_id == br_id, BrCharSide.character_id == 7)
        )).scalar_one_or_none()
        assert gone is None

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()
