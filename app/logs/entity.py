"""Split a gamelog target string into (character, ship) using the SDE ship-name set.

EVE logs non-damage targets in inconsistent layouts:
  - player:  "ShipType CharacterName [CORP] <ALLI>"  (ship concatenated, no delimiter)
  - new enc: "CharacterName [CORP][ALLI] ShipType"   (ship trailing)
  - NPC:     "Bhaalgorn"                              (bare type name, no character)
The reliable discriminator is the ship-type name itself, so we match against the SDE
ship/entity name dictionary rather than guessing an encoding.
"""

from __future__ import annotations

import re

# Strip corp [TICKER], alliance <TICKER> / &lt;TICKER&gt;, and any [bracket] groups.
_TICKER_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")


def _clean(text: str) -> str:
    s = _TICKER_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", s).strip()


def split_entity(text: str, entity_names: frozenset[str]) -> tuple[str | None, str | None]:
    """Return (character_name, ship_name). See module docstring."""
    cleaned = _clean(text or "")
    if not cleaned:
        return (None, None)
    if cleaned in entity_names:
        return (None, cleaned)  # bare NPC / ship name, no character

    words = cleaned.split(" ")
    # Longest leading run that is a known ship name → "ShipType CharacterName".
    for n in range(len(words) - 1, 0, -1):
        cand = " ".join(words[:n])
        if cand in entity_names:
            char = " ".join(words[n:]).strip()
            return (char or None, cand)
    # Longest trailing run that is a known ship name → "CharacterName ShipType".
    for n in range(len(words) - 1, 0, -1):
        cand = " ".join(words[len(words) - n:])
        if cand in entity_names:
            char = " ".join(words[: len(words) - n]).strip()
            return (char or None, cand)
    return (cleaned, None)  # unknown: character only
