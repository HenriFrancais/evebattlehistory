"""Per-loss item slot breakdown analytics for NV Battle Reports.

Given a killmail_id, groups KillmailItem rows by slot category (location),
resolves type names from InventoryType, and returns destroyed/dropped quantity
sums per slot along with individual item rows.

ISK value is NOT available (no per-item price source) — value is always None.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryType, KillmailItem

# Canonical slot order as specified in the task brief.
_SLOT_ORDER: list[str] = [
    "high",
    "med",
    "low",
    "rig",
    "subsystem",
    "drone_bay",
    "cargo",
    "implant",
    "other",
]


@dataclass
class ItemLossRow:
    type_id: int
    name: str
    location: str
    qty_destroyed: int
    qty_dropped: int


@dataclass
class SlotLoss:
    location: str
    destroyed_qty: int
    dropped_qty: int
    value: float | None = None  # No per-item price source — always None
    items: list[ItemLossRow] = field(default_factory=list)


@dataclass
class ItemLossBreakdown:
    killmail_id: int
    slots: list[SlotLoss]  # ordered per _SLOT_ORDER, only slots with items included


async def item_loss_breakdown(
    session: AsyncSession,
    killmail_id: int,
) -> ItemLossBreakdown:
    """Return per-slot item loss breakdown for one killmail.

    Groups KillmailItem rows by location (already stored as a category string,
    e.g. 'high', 'med', 'low', 'rig', 'subsystem', 'drone_bay', 'cargo',
    'implant', 'other').  Type names are resolved from InventoryType.

    Slots are ordered per the canonical list; only slots that contain at least
    one item are included.  value is always None (no price source).
    """
    # Fetch all items for this killmail
    item_rows = list(
        (
            await session.execute(
                select(
                    KillmailItem.item_idx,
                    KillmailItem.type_id,
                    KillmailItem.location,
                    KillmailItem.qty_destroyed,
                    KillmailItem.qty_dropped,
                ).where(KillmailItem.killmail_id == killmail_id)
            )
        ).all()
    )

    if not item_rows:
        return ItemLossBreakdown(killmail_id=killmail_id, slots=[])

    # Resolve type names in one query
    type_ids = {r[1] for r in item_rows}
    type_names: dict[int, str] = {}
    for inv in (
        await session.execute(
            select(InventoryType.type_id, InventoryType.name).where(
                InventoryType.type_id.in_(type_ids)
            )
        )
    ).all():
        type_names[inv[0]] = inv[1]

    # Group by location
    slots_map: dict[str, SlotLoss] = {}
    for _idx, type_id, location, qty_destroyed, qty_dropped in item_rows:
        if location not in slots_map:
            slots_map[location] = SlotLoss(
                location=location,
                destroyed_qty=0,
                dropped_qty=0,
                value=None,
                items=[],
            )
        slot = slots_map[location]
        slot.destroyed_qty += qty_destroyed
        slot.dropped_qty += qty_dropped
        slot.items.append(
            ItemLossRow(
                type_id=type_id,
                name=type_names.get(type_id, f"Unknown ({type_id})"),
                location=location,
                qty_destroyed=qty_destroyed,
                qty_dropped=qty_dropped,
            )
        )

    # Order slots by canonical order; unknown locations sort to end
    def _slot_rank(loc: str) -> int:
        try:
            return _SLOT_ORDER.index(loc)
        except ValueError:
            return len(_SLOT_ORDER)

    ordered_slots = sorted(slots_map.values(), key=lambda sl: _slot_rank(sl.location))

    return ItemLossBreakdown(killmail_id=killmail_id, slots=ordered_slots)
