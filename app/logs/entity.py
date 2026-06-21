"""Split a gamelog target string into (character, ship) using the SDE ship-name set.

EVE logs non-damage targets in inconsistent layouts:
  - player:  "ShipType CharacterName [CORP] <ALLI>"  (ship concatenated, no delimiter)
  - new enc: "CharacterName [CORP][ALLI] ShipType"   (ship trailing)
  - angled:  "ShipType [CORP] <CharacterName>"        (pilot inside the angle group)
  - NPC:     "Bhaalgorn"                              (bare type name, no character)
The reliable discriminator is the ship-type name itself, so we match against the SDE
ship/entity name dictionary rather than guessing an encoding.
"""

from __future__ import annotations

import re

# Strip corp [TICKER], alliance <TICKER> / &lt;TICKER&gt;, and any [bracket] groups.
_TICKER_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")
# Capture the contents of angle groups so a pilot rendered as <Name> can be recovered.
_ANGLE_RE = re.compile(r"&lt;([^&]*)&gt;|<([^>]*)>")


def _clean(text: str) -> str:
    s = _TICKER_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", s).strip()


def _char_from_angle(raw: str, entity_names: frozenset[str]) -> str | None:
    """Recover a pilot name rendered inside <…> brackets (the "ShipType [CORP]
    <CharacterName>" overview layout, where ``_clean`` discards it as a ticker).

    Corp/alliance tickers are at most 5 characters and never contain spaces, so an
    angle group that has a space or is longer than 5 characters — and is not itself
    a known ship/entity name — is the character, not a ticker.
    """
    for lt, plain in _ANGLE_RE.findall(raw):
        content: str = (lt or plain).strip()
        if content and content not in entity_names and (" " in content or len(content) > 5):
            return content
    return None


def split_entity(text: str, entity_names: frozenset[str]) -> tuple[str | None, str | None]:
    """Return (character_name, ship_name). See module docstring."""
    raw = text or ""
    cleaned = _clean(raw)
    char: str | None = None
    ship: str | None = None

    if cleaned:
        if cleaned in entity_names:
            ship = cleaned  # bare NPC / ship name, no character
        else:
            words = cleaned.split(" ")
            matched = False
            # Longest leading run that is a known ship name → "ShipType CharacterName".
            for n in range(len(words) - 1, 0, -1):
                cand = " ".join(words[:n])
                if cand in entity_names:
                    char = " ".join(words[n:]).strip() or None
                    ship = cand
                    matched = True
                    break
            if not matched:
                # Longest trailing run that is a known ship → "CharacterName ShipType".
                for n in range(len(words) - 1, 0, -1):
                    cand = " ".join(words[len(words) - n:])
                    if cand in entity_names:
                        char = " ".join(words[: len(words) - n]).strip() or None
                        ship = cand
                        matched = True
                        break
            if not matched:
                char = cleaned  # unknown: character only

    # "ShipType [CORP] <Pilot>" layout: the pilot is in the angle brackets that
    # _clean removed. Only recover it when no bare-word character was found, so the
    # "ShipType CharacterName [CORP] <ALLI>" layout (where <ALLI> is a ticker) is
    # never overridden.
    if char is None:
        char = _char_from_angle(raw, entity_names)

    return (char, ship)
