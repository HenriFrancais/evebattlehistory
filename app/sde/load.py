"""Load the processed SDE artifact into InventoryType, keyed by build number."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryType, SdeMeta
from app.observability.logging import log
from app.sde.process import read_manifest_build

SHIP_LIKE_CATEGORIES = {6, 11}  # Ship, Entity(NPC)


async def load_sde_into_db(session: AsyncSession, sde_dir: Path) -> int:
    """Upsert inventory_types.jsonl into InventoryType when the DB build is behind.
    Returns the number of rows upserted (0 if current or artifact missing)."""
    mf = sde_dir / "manifest.json"
    art = sde_dir / "inventory_types.jsonl"
    if not mf.exists() or not art.exists():
        return 0
    build = read_manifest_build(mf.read_text())
    if build is None:
        return 0
    meta = (await session.execute(select(SdeMeta).where(SdeMeta.id == 1))).scalar_one_or_none()
    if meta is not None and meta.build_number == build:
        return 0

    rows = [json.loads(line) for line in art.read_text().splitlines() if line.strip()]
    # SQLite caps bound variables per statement (~999 / 32766); chunk to stay safe.
    CHUNK = 2000
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        cstmt = sqlite_insert(InventoryType).values([
            {"type_id": r["type_id"], "name": r["name"], "group_id": r["group_id"],
             "group_name": r["group_name"], "category_id": r["category_id"],
             "category_name": r.get("category_name", "")}
            for r in chunk
        ])
        cstmt = cstmt.on_conflict_do_update(
            index_elements=["type_id"],
            set_={"name": cstmt.excluded.name, "group_id": cstmt.excluded.group_id,
                  "group_name": cstmt.excluded.group_name,
                  "category_id": cstmt.excluded.category_id},
        )
        await session.execute(cstmt)

    await session.merge(SdeMeta(id=1, build_number=build))
    log.info("sde.loaded", build=build, types=len(rows))
    return len(rows)


async def entity_name_set(session: AsyncSession) -> frozenset[str]:
    """Ship/entity (category 6/11) type names — the split_entity dictionary."""
    names = (
        await session.execute(
            select(InventoryType.name).where(InventoryType.category_id.in_(SHIP_LIKE_CATEGORIES))
        )
    ).scalars()
    return frozenset(n for n in names if n)
