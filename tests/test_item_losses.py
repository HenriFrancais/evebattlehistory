"""TDD tests for Task 19: per-loss item slot breakdown analytics + API endpoint.

Analytics contract:
  item_loss_breakdown(session, killmail_id) -> ItemLossBreakdown
  - Groups KillmailItem rows by location (flag_to_location category)
  - Resolves type names from InventoryType
  - Returns SlotLoss per category with destroyed_qty/dropped_qty sums + item rows
  - value is always None (no price source)
  - slots ordered: high, med, low, rig, subsystem, drone_bay, cargo, implant, other

API contract:
  GET /api/brs/{br_id}/losses/{killmail_id}/items
  - 200 with ItemLossBreakdownOut shape when killmail is in the BR
  - 404 when killmail is not linked to the given BR (guard via BR↔fight↔killmail join)
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    BattleReport,
    FightKill,
    InventoryType,
    Killmail,
    KillmailItem,
    SolarSystem,
)
from tests.conftest import MEMBER_HEADERS, TEST_TOKEN
from tests.test_e3_fleet_timeline import _make_br_with_fight

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_SOLAR_SYSTEM_ID = 31009876
_SHIP_TYPE_ID = 646  # Kestrel (arbitrary, must satisfy FK)

# Item type IDs used in tests
_HIGH_TYPE_ID = 2205   # some module (e.g. Afterburner)
_MED_TYPE_ID = 3244    # some med module
_LOW_TYPE_ID = 1319    # some low module
_CARGO_TYPE_ID = 34    # Tritanium (cargo)
_DRONE_TYPE_ID = 2488  # Hornet EC-300 (drone)


async def _ensure_prereqs(session) -> None:  # type: ignore[no-untyped-def]
    """Ensure SolarSystem + ship InventoryType rows exist."""
    if not (
        await session.execute(
            select(SolarSystem).where(SolarSystem.system_id == _SOLAR_SYSTEM_ID)
        )
    ).scalar_one_or_none():
        session.add(SolarSystem(system_id=_SOLAR_SYSTEM_ID, name="J-TestItem", security=None))
        await session.flush()

    for type_id, name in [
        (_SHIP_TYPE_ID, "Kestrel"),
        (_HIGH_TYPE_ID, "1MN Afterburner I"),
        (_MED_TYPE_ID, "Shield Extender I"),
        (_LOW_TYPE_ID, "Damage Control I"),
        (_CARGO_TYPE_ID, "Tritanium"),
        (_DRONE_TYPE_ID, "Hornet EC-300"),
    ]:
        if not (
            await session.execute(
                select(InventoryType).where(InventoryType.type_id == type_id)
            )
        ).scalar_one_or_none():
            session.add(InventoryType(type_id=type_id, name=name))
    await session.flush()


async def _seed_killmail_with_items(
    session, fight_id: int, km_id: int = 5000
) -> int:  # type: ignore[no-untyped-def]
    """Seed a Killmail with KillmailItem rows across high/low/cargo slots + a FightKill link.

    Items:
      - flag=11 (high slot 0): _HIGH_TYPE_ID, qty_destroyed=1, qty_dropped=0
      - flag=12 (high slot 1): _HIGH_TYPE_ID, qty_destroyed=0, qty_dropped=1
      - flag=27 (low slot 0): _LOW_TYPE_ID, qty_destroyed=1, qty_dropped=0
      - flag=5  (cargo):       _CARGO_TYPE_ID, qty_destroyed=0, qty_dropped=10
      - flag=87 (drone bay):   _DRONE_TYPE_ID, qty_destroyed=2, qty_dropped=3
    """
    await _ensure_prereqs(session)

    session.add(Killmail(
        killmail_id=km_id,
        killmail_time=dt.datetime(2026, 6, 15, 18, 0, 0, tzinfo=dt.UTC),
        solar_system_id=_SOLAR_SYSTEM_ID,
        victim_character_id=None,
        victim_ship_type_id=_SHIP_TYPE_ID,
        total_value=None,
        npc_kill=False,
        solo_kill=False,
    ))

    # Two high-slot items (same type, different destroyed/dropped)
    session.add(KillmailItem(
        killmail_id=km_id, item_idx=0,
        type_id=_HIGH_TYPE_ID, flag=11, location="high",
        qty_destroyed=1, qty_dropped=0, singleton=False,
    ))
    session.add(KillmailItem(
        killmail_id=km_id, item_idx=1,
        type_id=_HIGH_TYPE_ID, flag=12, location="high",
        qty_destroyed=0, qty_dropped=1, singleton=False,
    ))
    # Low slot
    session.add(KillmailItem(
        killmail_id=km_id, item_idx=2,
        type_id=_LOW_TYPE_ID, flag=27, location="low",
        qty_destroyed=1, qty_dropped=0, singleton=False,
    ))
    # Cargo
    session.add(KillmailItem(
        killmail_id=km_id, item_idx=3,
        type_id=_CARGO_TYPE_ID, flag=5, location="cargo",
        qty_destroyed=0, qty_dropped=10, singleton=False,
    ))
    # Drone bay
    session.add(KillmailItem(
        killmail_id=km_id, item_idx=4,
        type_id=_DRONE_TYPE_ID, flag=87, location="drone_bay",
        qty_destroyed=2, qty_dropped=3, singleton=False,
    ))

    session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=1))
    await session.flush()
    return km_id


# ---------------------------------------------------------------------------
# Analytics tests (Step 1 — RED before implementation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_loss_breakdown_slot_sums(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """item_loss_breakdown returns correct destroyed/dropped slot sums per location."""
    from app.analytics.item_losses import item_loss_breakdown

    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_killmail_with_items(s, fight_id, km_id=5000)
        await s.commit()

    async with db_session_maker() as s:
        breakdown = await item_loss_breakdown(s, km_id)

    assert breakdown.killmail_id == km_id

    # Build a dict slot_name → SlotLoss for easy assertion
    by_slot = {sl.location: sl for sl in breakdown.slots}

    # high slot: 1 destroyed, 1 dropped (from two items)
    assert "high" in by_slot
    high = by_slot["high"]
    assert high.destroyed_qty == 1
    assert high.dropped_qty == 1
    assert high.value is None  # no price source

    # low slot: 1 destroyed, 0 dropped
    assert "low" in by_slot
    low = by_slot["low"]
    assert low.destroyed_qty == 1
    assert low.dropped_qty == 0
    assert low.value is None

    # cargo: 0 destroyed, 10 dropped
    assert "cargo" in by_slot
    cargo = by_slot["cargo"]
    assert cargo.destroyed_qty == 0
    assert cargo.dropped_qty == 10
    assert cargo.value is None

    # drone_bay: 2 destroyed, 3 dropped
    assert "drone_bay" in by_slot
    drone = by_slot["drone_bay"]
    assert drone.destroyed_qty == 2
    assert drone.dropped_qty == 3

    # Slots with no items should NOT appear
    assert "med" not in by_slot
    assert "rig" not in by_slot
    assert "subsystem" not in by_slot


@pytest.mark.asyncio
async def test_item_loss_breakdown_item_rows_names(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """item_loss_breakdown resolves type names from InventoryType + returns item rows."""
    from app.analytics.item_losses import item_loss_breakdown

    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_killmail_with_items(s, fight_id, km_id=5001)
        await s.commit()

    async with db_session_maker() as s:
        breakdown = await item_loss_breakdown(s, km_id)

    by_slot = {sl.location: sl for sl in breakdown.slots}

    # high slot has two item rows (same type_id in two different flag positions)
    high = by_slot["high"]
    assert len(high.items) == 2
    for item in high.items:
        assert item.type_id == _HIGH_TYPE_ID
        assert item.name == "1MN Afterburner I"
        assert item.location == "high"

    # low slot: one item row
    low = by_slot["low"]
    assert len(low.items) == 1
    assert low.items[0].type_id == _LOW_TYPE_ID
    assert low.items[0].name == "Damage Control I"

    # cargo: one item row, 10 dropped
    cargo = by_slot["cargo"]
    assert len(cargo.items) == 1
    assert cargo.items[0].type_id == _CARGO_TYPE_ID
    assert cargo.items[0].name == "Tritanium"
    assert cargo.items[0].qty_destroyed == 0
    assert cargo.items[0].qty_dropped == 10


@pytest.mark.asyncio
async def test_item_loss_breakdown_slot_order(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Returned slots follow canonical order: high,med,low,rig,subsystem,drone_bay,cargo,implant,other."""  # noqa: E501
    from app.analytics.item_losses import item_loss_breakdown

    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_killmail_with_items(s, fight_id, km_id=5002)
        await s.commit()

    async with db_session_maker() as s:
        breakdown = await item_loss_breakdown(s, km_id)

    canonical = ["high", "med", "low", "rig", "subsystem", "drone_bay", "cargo", "implant", "other"]
    present = [sl.location for sl in breakdown.slots]

    # The returned locations must appear in canonical order (subset is fine)
    indices = [canonical.index(loc) for loc in present if loc in canonical]
    assert indices == sorted(indices), f"Slot order wrong: {present}"


@pytest.mark.asyncio
async def test_item_loss_breakdown_empty(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """item_loss_breakdown returns empty slots list when killmail has no items."""
    from app.analytics.item_losses import item_loss_breakdown

    async with db_session_maker() as s:
        await _ensure_prereqs(s)
        km_id = 5010
        s.add(Killmail(
            killmail_id=km_id,
            killmail_time=dt.datetime(2026, 6, 15, 18, 0, 0, tzinfo=dt.UTC),
            solar_system_id=_SOLAR_SYSTEM_ID,
            victim_character_id=None,
            victim_ship_type_id=_SHIP_TYPE_ID,
            npc_kill=False,
            solo_kill=False,
        ))
        await s.flush()
        await s.commit()

    async with db_session_maker() as s:
        breakdown = await item_loss_breakdown(s, km_id)

    assert breakdown.killmail_id == km_id
    assert breakdown.slots == []


# ---------------------------------------------------------------------------
# API contract tests (Step 5 — RED before endpoint implementation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_items_endpoint_returns_breakdown_shape(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/brs/{br_id}/losses/{km_id}/items returns correct JSON shape."""
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
    sm = get_sessionmaker(settings)

    async with sm() as s:
        br_id, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_killmail_with_items(s, fight_id, km_id=6000)
        await s.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        r = client.get(f"/api/brs/{br_id}/losses/{km_id}/items", headers=MEMBER_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["killmail_id"] == km_id
        assert "slots" in body

        by_slot = {sl["location"]: sl for sl in body["slots"]}

        # high slot present with correct counts
        assert "high" in by_slot
        high = by_slot["high"]
        assert high["destroyed_qty"] == 1
        assert high["dropped_qty"] == 1
        assert high["value"] is None
        assert len(high["items"]) == 2

        # cargo
        assert "cargo" in by_slot
        assert by_slot["cargo"]["dropped_qty"] == 10

        # drone_bay
        assert "drone_bay" in by_slot
        assert by_slot["drone_bay"]["destroyed_qty"] == 2
        assert by_slot["drone_bay"]["dropped_qty"] == 3

        # item rows have expected fields
        item0 = high["items"][0]
        assert "type_id" in item0
        assert "name" in item0
        assert "location" in item0
        assert "qty_destroyed" in item0
        assert "qty_dropped" in item0


@pytest.mark.asyncio
async def test_items_endpoint_404_killmail_not_in_br(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET .../losses/{km_id}/items → 404 when killmail is not in the given BR."""
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
    sm = get_sessionmaker(settings)

    async with sm() as s:
        # BR A with km 6100 linked
        br_id_a, fight_id_a = await _make_br_with_fight(s)
        km_id = await _seed_killmail_with_items(s, fight_id_a, km_id=6100)
        # BR B with no kills
        br_id_b = str(uuid.uuid4())
        s.add(BattleReport(
            br_id=br_id_b,
            source="demo",
            source_url="http://x",
            source_ref="ref",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        ))
        await s.flush()
        await s.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        # km belongs to br_a, asking for br_b → 404
        r = client.get(
            f"/api/brs/{br_id_b}/losses/{km_id}/items",
            headers=MEMBER_HEADERS,
        )
        assert r.status_code == 404

        # Correct BR → 200
        r2 = client.get(
            f"/api/brs/{br_id_a}/losses/{km_id}/items",
            headers=MEMBER_HEADERS,
        )
        assert r2.status_code == 200
