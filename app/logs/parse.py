"""EVE gamelog combat-line parser — pure functions, no I/O, no DB.

Two name encodings appear in real logs:
  NEW (damage, neut-out, disrupt/scram targets, rep-armor-to, rep-shield-by):
      CharName [CORP][ALLI] ShipType
  OLD (rep-armor-by, cap-to, cap-by):
      ShipType [ALLI][CORP] [CharName]

``parse_line`` never raises; malformed / truncated lines return None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

# --------------------------------------------------------------------------- #
#  HTML / EVE markup stripping
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
_NBSP_RE = re.compile(r"&nbsp;")


def strip_eve_markup(s: str) -> str:
    """Remove all HTML/EVE color+font tags from *s*; collapse excess whitespace minimally."""
    s = _TAG_RE.sub("", s)
    s = _NBSP_RE.sub(" ", s)
    # collapse runs of spaces to a single space
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# --------------------------------------------------------------------------- #
#  Line-envelope parsing
# --------------------------------------------------------------------------- #

_ENVELOPE_RE = re.compile(
    r"^\[ (\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2}):(\d{2}) \] \((\w+)\) (.*)$",
    re.DOTALL,
)


def _parse_ts(m: re.Match[str]) -> datetime:
    """Return a naive UTC datetime (no tzinfo). The system uses naive-UTC throughout.

    Fix (A): previously emitted tz-aware datetimes which caused TypeError when
    SQLAlchemy's synchronize_session='evaluate' compared LogEvent.ts (aware) against
    fight window bounds derived from Fight.started_at (naive after SQLite read-back).
    """
    return datetime(
        int(m.group(1)), int(m.group(2)), int(m.group(3)),
        int(m.group(4)), int(m.group(5)), int(m.group(6)),
    )


# --------------------------------------------------------------------------- #
#  Name-encoding parsers
# --------------------------------------------------------------------------- #

# NEW encoding: Name [CORP][ALLI] Ship  (appears after stripping HTML)
# Example stripped: "FakeEnemy Delta [10MN][.EFG] Retribution"
_NEW_ENC_RE = re.compile(
    r"^([\w' \-\.]+?)\s+\[([^\]]*)\]\[?([^\]]*?)\]?\s+([\w' \-]+[\w])$"
)

# NEW encoding simpler: Name [CORP][ALLI] Ship — the ALLI bracket may be absent
_NEW_ENC_RE2 = re.compile(
    r"^([\w' \-\.]+?)\s+\[([^\]]*)\]\s+([\w' \-]+[\w])$"
)

# OLD encoding: Ship [ALLI] [CORP] [CharName]
# Example stripped: "Guardian [NV] [NVACA] [AllyChar Amoni]"
# or                "Basilisk [NV] [NVACA] [AllyChar Tari]"
_OLD_ENC_RE = re.compile(
    r"^([\w' \-]+?)\s+\[([^\]]*)\]\s+\[?([^\]]*?)\]?\s+\[([^\]]+)\]$"
)


def _parse_new_encoding(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Return (name, corp_ticker, alliance_ticker, ship)."""
    text = text.strip()
    m = _NEW_ENC_RE.match(text)
    if m:
        name, corp, alli, ship = (m.group(1).strip(), m.group(2).strip() or None,
                                  m.group(3).strip() or None, m.group(4).strip())
        return name, corp, alli, ship
    m2 = _NEW_ENC_RE2.match(text)
    if m2:
        return m2.group(1).strip(), m2.group(2).strip() or None, None, m2.group(3).strip()
    return text, None, None, None


def _parse_old_encoding(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Return (name, corp_ticker, alli_ticker, ship) — OLD format has Ship first, CharName last."""
    text = text.strip()
    m = _OLD_ENC_RE.match(text)
    if m:
        ship = m.group(1).strip()
        alli = m.group(2).strip() or None
        corp = m.group(3).strip() or None
        char_name = m.group(4).strip()
        return char_name, corp, alli, ship
    return text, None, None, None


# --------------------------------------------------------------------------- #
#  Damage line (from / to)
# --------------------------------------------------------------------------- #

# After stripping markup, damage lines look like:
#   "432  from FakeEnemy Bravo[.TST](Brutix Navy Issue)  - 250mm Railgun II - Grazes"
# We match before stripping (raw HTML form) for accuracy on brackets embedded in names
_DAMAGE_RE = re.compile(
    r"^(\d+)\s+(from|to)\s+([\w' \-\.]+)\[([^\]]*)\]\(([^)]+)\)\s+-\s+(.+?)\s+-\s+(\w[\w ]+\w)$"
)


def _match_damage(rest: str) -> dict[str, Any] | None:
    """Match damage in/out line on the already-stripped *rest*."""
    m = _DAMAGE_RE.match(rest.strip())
    if not m:
        return None
    direction: Literal["in", "out"] = "in" if m.group(2) == "from" else "out"
    return {
        "effect_type": "damage",
        "direction": direction,
        "amount": float(m.group(1)),
        "other_name": m.group(3).strip(),
        "other_corp_ticker": m.group(4).strip() or None,
        "other_alliance_ticker": None,
        "other_ship_name": m.group(5).strip(),
        "module_name": m.group(6).strip(),
        "quality": m.group(7).strip(),
    }


# --------------------------------------------------------------------------- #
#  Warp disrupt / scram
# --------------------------------------------------------------------------- #

_EWAR_RE = re.compile(
    r"^Warp (disruption|scramble) attempt from (.+?) to (.+)$",
    re.DOTALL,
)


def _match_ewar(rest_stripped: str, rest_raw: str) -> dict[str, Any] | None:
    """Match warp disruption/scramble lines.

    Extracts BOTH parties for every line, plus an ``authoritative`` flag that is
    True iff one party is the log owner ("you"). Case 3 (third-party observation,
    neither party is "you") keeps the real source->target instead of folding the
    initiator into the log owner.
    """
    m = _EWAR_RE.match(rest_stripped)
    if not m:
        return None
    ewar_type = "disrupt" if m.group(1) == "disruption" else "scram"
    src_raw = m.group(2).strip()
    tgt_raw = m.group(3).strip()
    src_is_you = src_raw == "you"
    tgt_is_you = tgt_raw.rstrip("!") == "you"

    source_name: str | None = None if src_is_you else _parse_new_encoding(src_raw)[0]
    target_name: str | None = None if tgt_is_you else _parse_new_encoding(tgt_raw)[0]
    authoritative = src_is_you or tgt_is_you

    if src_is_you:
        direction: Literal["in", "out"] = "out"
        name, corp, alli, ship = _parse_new_encoding(tgt_raw)
    elif tgt_is_you:
        direction = "in"
        name, corp, alli, ship = _parse_new_encoding(src_raw)
    else:
        # Third-party: record the REAL initiator (source), never the log owner.
        direction = "in"
        name, corp, alli, ship = _parse_new_encoding(src_raw)

    return {
        "effect_type": ewar_type,
        "direction": direction,
        "amount": None,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": None,
        "quality": None,
        "source_name": source_name,
        "target_name": target_name,
        "authoritative": authoritative,
    }


# --------------------------------------------------------------------------- #
#  Energy neutralized (neut-out)
# --------------------------------------------------------------------------- #

# Stripped form: "234 GJ energy neutralized Target [ALLI][CORP] Ship - Module"
_NEUT_OUT_RE = re.compile(
    r"^(\d+)\s+GJ energy neutralized\s+(.+?)\s+-\s+(.+)$",
    re.DOTALL,
)


def _match_neut_out(rest_stripped: str) -> dict[str, Any] | None:
    m = _NEUT_OUT_RE.match(rest_stripped)
    if not m:
        return None
    target_part = m.group(2).strip()
    name, corp, alli, ship = _parse_new_encoding(target_part)
    return {
        "effect_type": "neut",
        "direction": "out",
        "amount": float(m.group(1)),
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": m.group(3).strip(),
        "quality": None,
    }


# --------------------------------------------------------------------------- #
#  Energy drained (nosferatu — NOS)
# --------------------------------------------------------------------------- #

# Stripped form (player-vs-player, outgoing, NEW encoding):
#   "+52 GJ energy drained from iamamusing Shazih [SMAD][PSAZ] Loki
#    - Small Ghoul Compact Energy Nosferatu"
# Stripped form (OLD encoding, player-vs-player, outgoing):
#   "+35 GJ energy drained from Leshak [4CRAB] [SRG-C] [zethx] - Medium Energy Nosferatu II"
# Incoming (enemy NOS on the listener):
#   "-0 GJ energy drained to Absolution [4CRAB] [SRG-C] [Hekpoc Risalo] - Small Energy Nosferatu II"
# NPC (bare name, outgoing):
#   "+10 GJ energy drained from Sleepless Sentinel - Small Energy Nosferatu II"
#
# Direction: "drained from <X>" → listener drains from X → outgoing ("out").
#            "drained to <X>"   → X drains from listener → incoming ("in").
# The signed amount is kept (positive = gained, negative = lost).
# The module is always the LISTENER's own nosferatu.
# The other party uses OLD encoding when brackets are present; bare name for NPCs.
_NOS_RE = re.compile(
    r"^([+-]?\d+)\s+GJ energy drained (from|to)\s+(.+?)\s+-\s+-?\s*(.+)$",
    re.DOTALL,
)


def _match_nos(rest_stripped: str) -> dict[str, Any] | None:
    """Match nosferatu (energy drained from/to) lines.

    Real-log finding: no incoming cap-warfare (energy neutralized <you>) lines were found
    in ~52 real gamelog files. Incoming NOS ("energy drained to <X>") IS logged client-side
    with a negative signed amount and OLD encoding. Outgoing NOS ("energy drained from <X>")
    uses OLD encoding for player targets and bare name for NPCs.
    """
    m = _NOS_RE.match(rest_stripped)
    if not m:
        return None
    amount_str = m.group(1)
    drain_word = m.group(2)  # "from" or "to"
    party_part = m.group(3).strip()
    module_name = m.group(4).strip()

    direction: Literal["in", "out"] = "out" if drain_word == "from" else "in"
    # OLD encoding for player targets; bare name falls back gracefully
    name, corp, alli, ship = _parse_old_encoding(party_part)

    return {
        "effect_type": "nos",
        "direction": direction,
        "amount": float(amount_str),
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": module_name,
        "quality": None,
    }


# --------------------------------------------------------------------------- #
#  Remote armor / shield rep  (to/by)
# --------------------------------------------------------------------------- #

# "remote armor repaired to" — NEW format: CharName [CORP][ALLI] Ship - Module
# "remote armor repaired by" — OLD format: Ship [ALLI][CORP] [CharName] - Module

_REP_RE = re.compile(
    r"^(\d+)\s+remote (armor repaired|shield boosted) (to|by)\s+(.+?)\s+-\s+-?\s*(.+)$",
    re.DOTALL,
)


def _match_rep(rest_stripped: str) -> dict[str, Any] | None:
    m = _REP_RE.match(rest_stripped)
    if not m:
        return None
    amount = float(m.group(1))
    rep_kind = m.group(2)  # "armor repaired" or "shield boosted"
    direction_word = m.group(3)  # "to" or "by"
    party_part = m.group(4).strip()
    module_name = m.group(5).strip()

    effect_type = "rep_armor" if "armor" in rep_kind else "rep_shield"
    direction: Literal["in", "out"] = "out" if direction_word == "to" else "in"

    # OLD format uses "by" for armor, NEW format uses "by" for shield (confirmed in logs)
    # Shield "boosted by" uses NEW format; armor "repaired by" uses OLD format
    if direction_word == "by" and "armor" in rep_kind:
        name, corp, alli, ship = _parse_old_encoding(party_part)
    else:
        # "to" (armor out, new format) or "by shield" (new format)
        name, corp, alli, ship = _parse_new_encoding(party_part)

    return {
        "effect_type": effect_type,
        "direction": direction,
        "amount": amount,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": module_name,
        "quality": None,
    }


# --------------------------------------------------------------------------- #
#  Remote capacitor transfer (to/by)
# --------------------------------------------------------------------------- #

# OLD format for both "to" and "by": Ship [ALLI][CORP] [CharName] - Module
_CAP_RE = re.compile(
    r"^(\d+)\s+remote capacitor transmitted (to|by)\s+(.+?)\s+-\s+-?\s*(.+)$",
    re.DOTALL,
)


def _match_cap(rest_stripped: str) -> dict[str, Any] | None:
    m = _CAP_RE.match(rest_stripped)
    if not m:
        return None
    amount = float(m.group(1))
    direction_word = m.group(2)  # "to" or "by"
    party_part = m.group(3).strip()
    module_name = m.group(4).strip()

    direction: Literal["in", "out"] = "out" if direction_word == "to" else "in"
    name, corp, alli, ship = _parse_old_encoding(party_part)

    return {
        "effect_type": "cap_transfer",
        "direction": direction,
        "amount": amount,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": module_name,
        "quality": None,
    }


# --------------------------------------------------------------------------- #
#  ECM jam (notify tag)
# --------------------------------------------------------------------------- #

_JAM_RE = re.compile(
    r"^Interference from (.+?)'s warp prevents your sensors from locking the target\.$"
)


def _match_jam(tag: str, rest_stripped: str) -> dict[str, Any] | None:
    if tag != "notify":
        return None
    m = _JAM_RE.match(rest_stripped)
    if not m:
        return None
    return {
        "effect_type": "jam",
        "direction": "in",
        "amount": None,
        "other_name": m.group(1).strip(),
        "other_corp_ticker": None,
        "other_alliance_ticker": None,
        "other_ship_name": None,
        "module_name": None,
        "quality": None,
    }


# --------------------------------------------------------------------------- #
#  ParsedLogEvent dataclass
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedLogEvent:
    ts: datetime
    tag: str
    direction: Literal["in", "out"] | None
    effect_type: str | None
    amount: float | None
    quality: str | None
    other_name: str | None
    other_corp_ticker: str | None
    other_alliance_ticker: str | None
    other_ship_name: str | None
    module_name: str | None
    raw: str
    source_name: str | None = None
    target_name: str | None = None
    authoritative: bool = False


# --------------------------------------------------------------------------- #
#  parse_line
# --------------------------------------------------------------------------- #

_EMPTY_EFFECT: dict[str, Any] = {
    "effect_type": None,
    "direction": None,
    "amount": None,
    "other_name": None,
    "other_corp_ticker": None,
    "other_alliance_ticker": None,
    "other_ship_name": None,
    "module_name": None,
    "quality": None,
    "source_name": None,
    "target_name": None,
    "authoritative": False,
}


def parse_line(line: str) -> ParsedLogEvent | None:
    """Parse one raw log line into a ``ParsedLogEvent`` or return ``None``.

    Returns ``None`` for:
    - blank lines
    - header / separator lines
    - lines that don't match the ``[ ts ] (tag) rest`` envelope

    Returns a ``ParsedLogEvent`` with ``effect_type=None`` for envelope-valid
    lines whose content is not a recognised effect (e.g. misses, hints).
    """
    if not line.strip():
        return None

    env = _ENVELOPE_RE.match(line.strip())
    if not env:
        return None

    try:
        ts = _parse_ts(env)
    except (ValueError, OverflowError):
        return None

    tag = env.group(7)
    rest_raw = env.group(8) or ""
    rest_stripped = strip_eve_markup(rest_raw)

    effect: dict[str, Any] | None = None

    if tag == "combat":
        # Try matchers in priority order
        effect = _match_damage(rest_stripped)
        if effect is None:
            effect = _match_ewar(rest_stripped, rest_raw)
        if effect is None:
            effect = _match_neut_out(rest_stripped)
        if effect is None:
            effect = _match_nos(rest_stripped)
        if effect is None:
            effect = _match_rep(rest_stripped)
        if effect is None:
            effect = _match_cap(rest_stripped)
    elif tag == "notify":
        effect = _match_jam(tag, rest_stripped)

    if effect is None:
        effect = _EMPTY_EFFECT.copy()

    return ParsedLogEvent(
        ts=ts,
        tag=tag,
        direction=effect.get("direction"),
        effect_type=effect.get("effect_type"),
        amount=effect.get("amount"),
        quality=effect.get("quality"),
        other_name=effect.get("other_name"),
        other_corp_ticker=effect.get("other_corp_ticker"),
        other_alliance_ticker=effect.get("other_alliance_ticker"),
        other_ship_name=effect.get("other_ship_name"),
        module_name=effect.get("module_name"),
        raw=line,
        source_name=effect.get("source_name"),
        target_name=effect.get("target_name"),
        authoritative=bool(effect.get("authoritative")),
    )


# --------------------------------------------------------------------------- #
#  ParsedLog + parse_log
# --------------------------------------------------------------------------- #

from app.logs.filename import LogHeader, parse_header  # noqa: E402  (avoid circular)


@dataclass
class ParsedLog:
    header: LogHeader
    events: list[ParsedLogEvent]
    stats: dict[str, int]


def parse_log(text: str) -> ParsedLog:
    """Parse an entire gamelog file text, returning structured output + quality stats."""
    header = parse_header(text)
    events: list[ParsedLogEvent] = []
    total_lines = 0
    combat_lines = 0
    matched = 0
    unmatched_combat = 0

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        evt = parse_line(raw_line)
        if evt is None:
            continue
        total_lines += 1
        events.append(evt)
        if evt.tag == "combat":
            combat_lines += 1
            if evt.effect_type is not None:
                matched += 1
            else:
                unmatched_combat += 1

    stats: dict[str, int] = {
        "total_lines": total_lines,
        "combat_lines": combat_lines,
        "matched": matched,
        "unmatched_combat": unmatched_combat,
    }
    return ParsedLog(header=header, events=events, stats=stats)
