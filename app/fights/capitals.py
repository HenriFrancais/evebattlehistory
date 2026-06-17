from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

_DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "capital_ships.json"
_CAPITAL_NAMES: frozenset[str] | None = None


def _load() -> frozenset[str]:
    global _CAPITAL_NAMES
    if _CAPITAL_NAMES is None:
        data = json.loads(_DATA_FILE.read_text())
        names: set[str] = set()
        for hull_list in data.values():
            names.update(n.lower() for n in hull_list)
        _CAPITAL_NAMES = frozenset(names)
    return _CAPITAL_NAMES


def is_capital_type_name(name: str) -> bool:
    return name.lower() in _load()


async def backfill_capitals(session: AsyncSession) -> int:
    """Set capitals_involved=True on any fight that has a capital ship in fight_ship_counts.

    Uses a single JOIN query to fetch (fight_id, ship_name) pairs for all fights in
    one round-trip, avoiding the previous N+1 pattern.  Returns count of fights updated.
    """
    from sqlalchemy import select

    from app.db.models import Fight, FightShipCount, InventoryType

    capital_names = _load()

    # Single query: join fight_ship_count → inventory_type for all fights at once.
    rows_result = await session.execute(
        select(FightShipCount.fight_id, InventoryType.name)
        .join(InventoryType, InventoryType.type_id == FightShipCount.ship_type_id)
        .distinct()
    )
    rows = list(rows_result)

    # Build set of fight_ids that have at least one capital ship.
    capital_fight_ids: set[int] = {
        fight_id for fight_id, name in rows if name.lower() in capital_names
    }

    if not capital_fight_ids:
        return 0

    # Load only the fights that need updating (capitals_involved is currently False).
    fights_result = await session.execute(
        select(Fight).where(
            Fight.fight_id.in_(capital_fight_ids),
            Fight.capitals_involved.is_(False),
        )
    )
    fights = list(fights_result.scalars())

    for fight in fights:
        fight.capitals_involved = True

    await session.flush()
    return len(fights)
