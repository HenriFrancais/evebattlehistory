"""Fleet composition: per-side ship counts, per-pilot roster, and (for elevated
callers) char→user grouping. Killmail-derived: a pilot is any character seen as
a Killmail victim or a KillmailAttacker across the BR's fights.

Each pilot maps to exactly one ship and one side:
  - ship: the victim ship if the pilot died, else their most-frequent attacker ship.
  - side: classify_entity(alliance_id, corp_id, baseline, overrides).
Consistent with the kill-marker classification on the fleet timeline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import _resolve_char_names
from app.analytics.sides_config import EntityKey, classify_entity
from app.config import Settings
from app.db.models import (
    BrFight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
)

_SIDE_ORDER = ("friendly", "hostile", "unassigned")


@dataclass
class CompositionPilot:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    user_name: str | None


@dataclass
class CompositionShip:
    ship_type_id: int
    ship_name: str
    count: int


@dataclass
class CompositionSide:
    side_kind: str
    pilot_count: int
    ships: list[CompositionShip]
    pilots: list[CompositionPilot]


@dataclass
class CompositionResult:
    sides: list[CompositionSide]


@dataclass
class _Pilot:
    side: str
    ship_type_id: int | None
    lost: bool
    attacker_ships: Counter[int]  # ship_type_id -> occurrences (for non-victims)


async def fleet_composition(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
    settings: Settings,
    char_to_user: dict[int, str] | None,
) -> CompositionResult:
    """Build per-side composition for *br_id*. See module docstring."""
    km_ids = list(
        (
            await session.execute(
                select(FightKill.killmail_id)
                .join(BrFight, BrFight.fight_id == FightKill.fight_id)
                .where(BrFight.br_id == br_id)
            )
        ).scalars()
    )
    if not km_ids:
        return CompositionResult(sides=[])

    def _side(alli: int | None, corp: int | None) -> str:
        return classify_entity(
            alli, corp, baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps, overrides=overrides,
        )

    pilots: dict[int, _Pilot] = {}

    # Victims first — authoritative ship + side, lost=True.
    for char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                Killmail.victim_character_id,
                Killmail.victim_ship_type_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        pilots[char_id] = _Pilot(side=_side(alli, corp), ship_type_id=ship_id,
                                 lost=True, attacker_ships=Counter())

    # Attackers — fill in pilots who didn't die; accumulate candidate ships.
    for char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                KillmailAttacker.ship_type_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        p = pilots.get(char_id)
        if p is None:
            p = _Pilot(side=_side(alli, corp), ship_type_id=None, lost=False,
                       attacker_ships=Counter())
            pilots[char_id] = p
        if not p.lost and ship_id is not None:
            p.attacker_ships[ship_id] += 1

    # Resolve each non-victim pilot's ship to its most common attacker ship.
    for p in pilots.values():
        if not p.lost and p.ship_type_id is None and p.attacker_ships:
            p.ship_type_id = p.attacker_ships.most_common(1)[0][0]

    # Resolve names.
    char_names = await _resolve_char_names(session, settings, set(pilots))
    ship_ids = {p.ship_type_id for p in pilots.values() if p.ship_type_id is not None}
    ship_names: dict[int, str] = {}
    if ship_ids:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.type_id.in_(ship_ids)))
        ).scalars():
            ship_names[inv.type_id] = inv.name

    # Group into sides.
    by_side: dict[str, list[CompositionPilot]] = {}
    for char_id, p in pilots.items():
        by_side.setdefault(p.side, []).append(
            CompositionPilot(
                character_id=char_id,
                character_name=char_names.get(char_id) or f"Char {char_id}",
                ship_type_id=p.ship_type_id,
                ship_name=(ship_names.get(p.ship_type_id, "Unknown")
                           if p.ship_type_id is not None else "Unknown"),
                lost=p.lost,
                user_name=(char_to_user.get(char_id) if char_to_user else None),
            )
        )

    sides: list[CompositionSide] = []
    for side_kind in _SIDE_ORDER:
        plist = by_side.get(side_kind)
        if not plist:
            continue
        plist.sort(key=lambda x: (x.ship_name, x.character_name))
        counts: Counter[int] = Counter()
        for pilot in plist:
            if pilot.ship_type_id is not None:
                counts[pilot.ship_type_id] += 1
        ships = [
            CompositionShip(ship_type_id=sid, ship_name=ship_names.get(sid, "Unknown"), count=c)
            for sid, c in counts.most_common()
        ]
        sides.append(CompositionSide(side_kind=side_kind, pilot_count=len(plist),
                                     ships=ships, pilots=plist))
    return CompositionResult(sides=sides)
