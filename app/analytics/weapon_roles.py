"""Weapon-role and range-band mapping for killmail augmentation.

Maps an EVE weapon/module (identified by SDE InventoryType fields) to a
coarse role (turret/missile/drone/smartbomb/ewar/tackle/other) and a range
band (short/medium/long/none).

The SDE snapshot used here contains only name/group_name/category_id — no
range attributes — so band is derived from weapon family rather than exact
numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.weapons import classify_weapon

# ---------------------------------------------------------------------------
# classify_weapon returns WeaponClass(category, fallback_name).
# category values: hybrid|projectile|laser|rocket|torpedo|missile|smartbomb|bomb|other
# ---------------------------------------------------------------------------

_TURRET_CATEGORIES = frozenset({"hybrid", "projectile", "laser"})
_MISSILE_CATEGORIES = frozenset({"missile", "rocket", "torpedo"})

# Turret band is name-driven (short/medium/long not reliable without range attrs)
# so we use 'none' as a safe default for all bands.
_TURRET_BAND: dict[str, str] = {
    "hybrid": "medium",
    "projectile": "medium",
    "laser": "long",
    "rocket": "short",
    "torpedo": "long",
    "missile": "medium",
}

_TACKLE_GROUPS = frozenset({"Warp Scrambler", "Warp Disruptor", "Stasis Web"})
_EWAR_GROUPS = frozenset({"ECM", "Sensor Dampener", "Target Painter", "Weapon Disruptor"})

# category_id 18 = Drone in the EVE SDE
_DRONE_CATEGORY_ID = 18


@dataclass(frozen=True)
class WeaponTypeInfo:
    type_id: int
    name: str | None
    group_name: str | None
    category_id: int


@dataclass(frozen=True)
class WeaponRole:
    role: str  # turret|missile|drone|smartbomb|ewar|tackle|other
    band: str  # short|medium|long|none


def weapon_role(info: WeaponTypeInfo) -> WeaponRole:
    """Map a weapon's SDE metadata to a coarse role and range band."""

    # 1. Drone category takes priority (category_id 18).
    if info.category_id == _DRONE_CATEGORY_ID:
        return WeaponRole("drone", "medium")

    # 2. Use classify_weapon for name-driven turret/missile/smartbomb families.
    wc = classify_weapon(info.name)
    if wc.category in _TURRET_CATEGORIES:
        return WeaponRole("turret", _TURRET_BAND.get(wc.category, "none"))
    if wc.category in _MISSILE_CATEGORIES:
        return WeaponRole("missile", _TURRET_BAND.get(wc.category, "none"))
    if wc.category == "smartbomb":
        return WeaponRole("smartbomb", "short")

    # 3. Group-name keyword tables for ewar/tackle (order: tackle first).
    group = info.group_name or ""
    if group in _TACKLE_GROUPS:
        return WeaponRole("tackle", "short")
    if group in _EWAR_GROUPS:
        return WeaponRole("ewar", "medium")

    return WeaponRole("other", "none")
