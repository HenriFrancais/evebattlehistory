"""Per-fight side rollups from the BR's side classification.

Replaces the old killmail 2-colouring (which merged both fleets onto one side in
messy brawls). Every victim and attacker is placed by the BR's ``SideResolver`` —
the same friendly / hostile / unassigned answer the UI shows — and the per-side
stats, victim sides and ship counts are derived from that.

Pure function, no DB access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.analytics.sides_config import SideResolver

#: Stable side_idx for the three classified sides (FightSide / FightKill /
#: FightShipCount primary-key column).
SIDE_IDX: dict[str, int] = {"friendly": 0, "hostile": 1, "unassigned": 2}


class _AttackerProto(Protocol):
    character_id: int | None
    alliance_id: int | None
    corporation_id: int | None
    ship_type_id: int | None


class _KillProto(Protocol):
    killmail_id: int
    victim_character_id: int | None
    victim_alliance_id: int | None
    victim_corporation_id: int | None
    victim_ship_type_id: int
    total_value: float | None
    attackers: list[_AttackerProto]


@dataclass
class SideStats:
    """Per-side aggregate stats for a fight."""

    isk_lost: float = 0.0
    pilot_count: int = 0
    alliance_ids: set[int] = field(default_factory=set)
    corp_ids: set[int] = field(default_factory=set)
    character_ids: set[int] = field(default_factory=set)
    #: ship_type_id → hulls fielded: every loss counts; a surviving attacker's
    #: hull counts once per pilot however many killmails they appear on.
    ship_counts: dict[int, int] = field(default_factory=dict)


def classified_fight_sides(
    kills: list[_KillProto], resolver: SideResolver
) -> tuple[dict[str, SideStats], dict[int, str]]:
    """Return ``({side_kind: SideStats}, {killmail_id: victim side_kind})``."""
    per_side: dict[str, SideStats] = {}
    victim_side: dict[int, str] = {}
    attacker_seen: set[tuple[str, int]] = set()

    def _add(sd: SideStats, alli: int | None, corp: int | None, char: int | None) -> None:
        if alli is not None:
            sd.alliance_ids.add(alli)
        if corp is not None:
            sd.corp_ids.add(corp)
        if char is not None:
            sd.character_ids.add(char)

    for k in kills:
        side = resolver.character(
            k.victim_character_id, k.victim_alliance_id, k.victim_corporation_id
        )
        victim_side[k.killmail_id] = side
        sd = per_side.setdefault(side, SideStats())
        sd.isk_lost += k.total_value or 0.0
        _add(sd, k.victim_alliance_id, k.victim_corporation_id, k.victim_character_id)
        sd.ship_counts[k.victim_ship_type_id] = sd.ship_counts.get(k.victim_ship_type_id, 0) + 1

        for att in k.attackers:
            if att.character_id is None and att.alliance_id is None and att.corporation_id is None:
                continue  # unattributable (e.g. bare NPC)
            a_side = resolver.character(att.character_id, att.alliance_id, att.corporation_id)
            asd = per_side.setdefault(a_side, SideStats())
            _add(asd, att.alliance_id, att.corporation_id, att.character_id)
            if att.ship_type_id is None:
                continue
            if att.character_id is not None:
                key = (a_side, att.character_id)
                if key in attacker_seen:
                    continue
                attacker_seen.add(key)
            asd.ship_counts[att.ship_type_id] = asd.ship_counts.get(att.ship_type_id, 0) + 1

    for sd in per_side.values():
        sd.pilot_count = len(sd.character_ids)
    return per_side, victim_side
