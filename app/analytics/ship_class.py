"""Hull size ordering for pilot lists: largest ship classes first.

The SDE gives every hull a group (Titan, Battleship, Logistics, ...). Battle report
tools conventionally list a fleet from its biggest hulls down to frigates and pods,
which is the order an FC reads a fight in. The rank is the group's position below;
an unknown group (or no hull at all) sorts last.
"""

from __future__ import annotations

_GROUPS_LARGEST_FIRST: tuple[str, ...] = (
    # Capitals
    "Titan",
    "Supercarrier",
    "Carrier",
    "Command Carrier",
    "Dreadnought",
    "Lancer Dreadnought",
    "Force Auxiliary",
    "Capital Industrial Ship",
    "Jump Freighter",
    "Freighter",
    "Industrial Command Ship",
    # Battleships
    "Marauder",
    "Black Ops",
    "Battleship",
    # Battlecruisers
    "Command Ship",
    "Combat Battlecruiser",
    "Attack Battlecruiser",
    # Cruisers
    "Strategic Cruiser",
    "Heavy Assault Cruiser",
    "Heavy Interdiction Cruiser",
    "Logistics",
    "Force Recon Ship",
    "Combat Recon Ship",
    "Flag Cruiser",
    "Cruiser",
    # Industrials and mining
    "Deep Space Transport",
    "Blockade Runner",
    "Hauler",
    "Expedition Command Ship",
    "Exhumer",
    "Mining Barge",
    # Destroyers
    "Command Destroyer",
    "Tactical Destroyer",
    "Interdictor",
    "Destroyer",
    # Frigates
    "Assault Frigate",
    "Electronic Attack Ship",
    "Logistics Frigate",
    "Covert Ops",
    "Stealth Bomber",
    "Interceptor",
    "Expedition Frigate",
    "Prototype Exploration Ship",
    "Frigate",
    # The rest
    "Special Edition Yachts",
    "Corvette",
    "Shuttle",
    "Capsule",
)

_RANK: dict[str, int] = {name: i for i, name in enumerate(_GROUPS_LARGEST_FIRST)}

#: Rank of a hull whose group is unknown, or of a pilot with no hull recorded.
UNKNOWN_RANK: int = len(_GROUPS_LARGEST_FIRST)


def ship_size_rank(group_name: str | None) -> int:
    """0 for the largest class (Titan); larger numbers for smaller hulls."""
    return _RANK.get(group_name or "", UNKNOWN_RANK)
