"""EVE gamelog filename + header identity parsing — pure functions, no I/O."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------- #
#  Filename parsing
# --------------------------------------------------------------------------- #

_FILENAME_RE = re.compile(r"^(\d{8})_(\d{6})(?:_(\d+))?\.txt$")


def parse_filename(name: str) -> dict[str, Any]:
    """Return ``{"start": datetime|None, "character_id": int|None}``."""
    m = _FILENAME_RE.match(name)
    if not m:
        return {"start": None, "character_id": None}
    date_str, time_str, char_id_str = m.group(1), m.group(2), m.group(3)
    try:
        start = datetime(
            int(date_str[:4]),
            int(date_str[4:6]),
            int(date_str[6:8]),
            int(time_str[:2]),
            int(time_str[2:4]),
            int(time_str[4:6]),
            tzinfo=UTC,
        )
    except ValueError:
        return {"start": None, "character_id": None}
    character_id = int(char_id_str) if char_id_str else None
    return {"start": start, "character_id": character_id}


# --------------------------------------------------------------------------- #
#  Header parsing
# --------------------------------------------------------------------------- #

_LISTENER_RE = re.compile(r"^\s+Listener:\s+(.+?)\s*$", re.MULTILINE)
_SESSION_RE = re.compile(
    r"^\s+Session Started:\s+(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)


@dataclass(frozen=True)
class LogHeader:
    listener_name: str | None
    session_started: datetime | None


def parse_header(text: str) -> LogHeader:
    """Extract listener name and session start from log header block."""
    lm = _LISTENER_RE.search(text)
    sm = _SESSION_RE.search(text)
    listener_name = lm.group(1).strip() if lm else None
    session_started: datetime | None = None
    if sm:
        raw_dt = sm.group(1)  # "2026.06.16 19:21:14"
        session_started = datetime.strptime(raw_dt, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=UTC
        )
    return LogHeader(listener_name=listener_name, session_started=session_started)


# --------------------------------------------------------------------------- #
#  Character resolution
# --------------------------------------------------------------------------- #


def resolve_character(
    filename_meta: dict[str, Any],
    header: LogHeader,
    roster_lookup: Callable[[str], int | None],
) -> dict[str, Any]:
    """Resolve character identity from filename charId or header listener name.

    Returns ``{"character_id": int|None, "character_name": str|None,
                "resolved_via": "filename"|"listener_roster"|"unresolved"}``.

    ``roster_lookup`` is a pure injected callable so this function stays testable.
    """
    # 1. Filename has authoritative charId
    if filename_meta.get("character_id") is not None:
        char_id: int = filename_meta["character_id"]
        return {
            "character_id": char_id,
            "character_name": header.listener_name,
            "resolved_via": "filename",
        }

    # 2. Try header listener name against roster
    if header.listener_name:
        resolved_id = roster_lookup(header.listener_name)
        if resolved_id is not None:
            return {
                "character_id": resolved_id,
                "character_name": header.listener_name,
                "resolved_via": "listener_roster",
            }

    # 3. Unresolved
    return {
        "character_id": None,
        "character_name": header.listener_name,
        "resolved_via": "unresolved",
    }
