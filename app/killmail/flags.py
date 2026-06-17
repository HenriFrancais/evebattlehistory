"""EVE killmail item flag → location category."""

from __future__ import annotations

# Reference: https://github.com/esi/esi-issues + EVE inventory flags.
HIGH_SLOT_FLAGS = set(range(11, 19))
MED_SLOT_FLAGS = set(range(19, 27))
LOW_SLOT_FLAGS = set(range(27, 35))
RIG_FLAGS = set(range(92, 96))
SUBSYSTEM_FLAGS = set(range(125, 133))
IMPLANT_FLAGS = {89, 90, 91, 96, 97, 98}
DRONE_BAY_FLAG = 87
CARGO_FLAG = 5


def flag_to_location(flag: int) -> str:
    """Map an EVE inventory flag to a location category."""
    if flag in HIGH_SLOT_FLAGS:
        return "high"
    if flag in MED_SLOT_FLAGS:
        return "med"
    if flag in LOW_SLOT_FLAGS:
        return "low"
    if flag in RIG_FLAGS:
        return "rig"
    if flag in SUBSYSTEM_FLAGS:
        return "subsystem"
    if flag == DRONE_BAY_FLAG:
        return "drone_bay"
    if flag == CARGO_FLAG:
        return "cargo"
    if flag in IMPLANT_FLAGS:
        return "implant"
    return "other"
