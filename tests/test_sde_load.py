import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_load_upserts_and_is_idempotent(db_session_maker, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import InventoryType
    from app.sde.load import load_sde_into_db, entity_name_set
    from sqlalchemy import select

    (tmp_path / "manifest.json").write_text(json.dumps({"buildNumber": 200}))
    (tmp_path / "inventory_types.jsonl").write_text(
        json.dumps({"type_id": 645, "name": "Dominix", "group_id": 27,
                    "group_name": "Battleship", "category_id": 6, "category_name": ""}) + "\n"
        + json.dumps({"type_id": 2488, "name": "Dual 150mm Railgun II", "group_id": 53,
                      "group_name": "Energy Weapon", "category_id": 7, "category_name": ""}) + "\n"
    )

    async with db_session_maker() as session:
        n = await load_sde_into_db(session, tmp_path)
        await session.commit()
    assert n == 2

    async with db_session_maker() as session:
        again = await load_sde_into_db(session, tmp_path)   # same build → skip
        await session.commit()
    assert again == 0

    async with db_session_maker() as session:
        names = await entity_name_set(session)
        row = (await session.execute(select(InventoryType).where(InventoryType.type_id == 645))).scalar_one()
    assert "Dominix" in names          # category 6 ship
    assert "Dual 150mm Railgun II" not in names  # category 7 weapon, not a ship
    assert row.name == "Dominix" and row.category_id == 6
