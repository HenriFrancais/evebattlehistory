"""Pure weapon-name classifier for damage log lines.

Maps an EVE module/weapon name (as it appears in a gamelog damage line) to a
weapon *category* and a canonical *fallback* module name. The caller resolves
the real module's icon by exact name first; when that misses (faction/abyssal
names), it resolves the family fallback name instead. Keyword order matters:
more specific terms (rocket, smartbomb) are checked before their generic
substrings (missile, bomb).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeaponClass:
    category: str
    fallback_name: str | None


# (substring, category, fallback_name) — first match wins; order is significant.
_KEYWORDS: tuple[tuple[str, str, str | None], ...] = (
    ("railgun", "hybrid", "250mm Railgun II"),
    ("blaster", "hybrid", "250mm Railgun II"),
    ("autocannon", "projectile", "425mm AutoCannon II"),
    ("artillery", "projectile", "425mm AutoCannon II"),
    ("pulse laser", "laser", "Mega Pulse Laser II"),
    ("beam laser", "laser", "Mega Pulse Laser II"),
    ("laser", "laser", "Mega Pulse Laser II"),
    ("rocket", "rocket", "Rocket Launcher II"),
    ("torpedo", "torpedo", "Torpedo Launcher II"),
    ("missile", "missile", "Heavy Missile Launcher II"),
    ("smartbomb", "smartbomb", "Large EMP Smartbomb II"),
    ("bomb", "bomb", None),
)


def classify_weapon(module_name: str | None) -> WeaponClass:
    """Classify *module_name* into a weapon family. See module docstring."""
    if not module_name:
        return WeaponClass("other", None)
    low = module_name.lower()
    for kw, category, fallback in _KEYWORDS:
        if kw in low:
            return WeaponClass(category, fallback)
    return WeaponClass("other", None)
