"""Fleet composition: per-side ship counts, per-pilot roster, and (for elevated
callers) char→user grouping. Killmail-derived: a pilot is any character seen as
a Killmail victim or a KillmailAttacker across the BR's fights.

Capsules (type_id 670) are excluded everywhere. Each character maps to a *hull
set* — the distinct non-capsule ships they flew as victim or attacker:
  - a character with >1 hull is a reship (every hull row flagged reship=True);
  - a capsule-only (podded) character becomes one hull-less "Unknown" row;
  - side: classify_entity(alliance_id, corp_id, baseline, overrides), victim wins.
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

CAPSULE_TYPE_ID = 670


@dataclass
class CompositionPilot:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    reship: bool
    user_name: str | None
    killmail_id: int | None


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
class _Acc:
    side: str
    hulls: dict[int, tuple[bool, int | None]]  # ship_type_id → (lost?, killmail_id)
    podded: bool            # appeared only in a capsule


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

    acc: dict[int, _Acc] = {}

    def _ensure(char_id: int, side: str) -> _Acc:
        a = acc.get(char_id)
        if a is None:
            a = _Acc(side=side, hulls={}, podded=False)
            acc[char_id] = a
        return a

    # Victims: authoritative side + a lost hull (capsules → podded, not a hull).
    for km_id, char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                Killmail.killmail_id,
                Killmail.victim_character_id,
                Killmail.victim_ship_type_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        a = _ensure(char_id, _side(alli, corp))
        a.side = _side(alli, corp)  # victim entity wins for side
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls[ship_id] = (True, km_id)
        elif ship_id == CAPSULE_TYPE_ID:
            a.podded = True

    # Attackers: side if unseen, plus any non-capsule hull they flew.
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
        a = acc.get(char_id) or _ensure(char_id, _side(alli, corp))
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls.setdefault(ship_id, (False, None))

    char_names = await _resolve_char_names(session, settings, set(acc))
    ship_ids = {sid for a in acc.values() for sid in a.hulls}
    ship_names: dict[int, str] = {}
    if ship_ids:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.type_id.in_(ship_ids)))
        ).scalars():
            ship_names[inv.type_id] = inv.name

    by_side: dict[str, list[CompositionPilot]] = {}
    for char_id, a in acc.items():
        name = char_names.get(char_id) or f"Char {char_id}"
        user = char_to_user.get(char_id) if char_to_user else None
        is_reship = len(a.hulls) > 1
        if a.hulls:
            for sid, (lost, km_id) in a.hulls.items():
                by_side.setdefault(a.side, []).append(
                    CompositionPilot(character_id=char_id, character_name=name, ship_type_id=sid,
                                     ship_name=ship_names.get(sid, "Unknown"), lost=lost,
                                     reship=is_reship, user_name=user, killmail_id=km_id)
                )
        else:
            # Capsule-only / no hull recorded.
            by_side.setdefault(a.side, []).append(
                CompositionPilot(character_id=char_id, character_name=name, ship_type_id=None,
                                 ship_name="Unknown", lost=a.podded, reship=False,
                                 user_name=user, killmail_id=None)
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
        pilot_count = len({p.character_id for p in plist})
        sides.append(CompositionSide(side_kind=side_kind, pilot_count=pilot_count,
                                     ships=ships, pilots=plist))
    return CompositionResult(sides=sides)
