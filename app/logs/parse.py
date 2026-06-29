"""EVE gamelog combat-line parser — pure functions, no I/O, no DB.

A counterparty's rendering is governed by the *logging* player's overview ship-label
settings, NOT by the message type — the same pilot may appear pilot-first, ship-first,
in italics, or by ship only, with tickers in [brackets], <angles>, or (parens), in any
order. So every effect resolves its counterparty through one ordered, overview-agnostic
resolver (`_resolve_counterparty`) rather than a per-effect encoding guess; adding a new
layout is a single new step there.

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

# A ship given a custom (user-entered) name renders that cosmetic name in italics —
# "<u>ShipType</u> <i>CustomName</i>]" — with the real pilot following in a separate
# [bracket]. The custom name is arbitrary text (brackets, unicode, punctuation) that
# otherwise gets mistaken for the pilot, so drop the whole decoration: an optional
# fused corp-ticker prefix "[CORP ", the "<i>..</i>" span, and a trailing "]".
# It is purely cosmetic and we never want it. (<i> is used for nothing else in logs.)
_CUSTOM_SHIP_NAME_RE = re.compile(r"(?:\[[^\[\]<]*)?<i>.*?</i>\]?", re.DOTALL)


def strip_eve_markup(s: str) -> str:
    """Remove all HTML/EVE color+font tags from *s*; collapse excess whitespace minimally."""
    s = _CUSTOM_SHIP_NAME_RE.sub(" ", s)
    s = _TAG_RE.sub("", s)
    s = _NBSP_RE.sub(" ", s)
    # collapse runs of spaces to a single space
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# Italics do double duty in EVE combat logs:
#   (a) PILOT-name label — some overviews render the counterparty's pilot in italics,
#       e.g. "<b><i>Body Cam Off</b></i>Heretic(NV)" — followed by the ship type;
#   (b) cosmetic custom SHIP name — closed by ']' (the ship-label bracket), e.g.
#       "<u>Zarmazd</u> <i>✖ DXa Zarming</i>] [Deringston Xa'thon]", or trailing after
#       " - ".
# strip_eve_markup deletes BOTH; to recover a pilot we remove only the cosmetic forms
# (closed by ']' or introduced by " - ") and keep a remaining free-standing label.
_COSMETIC_CLOSED_RE = re.compile(r"(?:\[[^\[\]<]*)?<i>.*?</i>\]", re.DOTALL)
_COSMETIC_SUFFIX_RE = re.compile(r"\s-\s*(?:<[^>]*>\s*)*<i>.*?</i>", re.DOTALL)
_ITALIC_RE = re.compile(r"<i>(.*?)</i>", re.DOTALL)


def _italic_pilot_label(raw: str) -> str | None:
    """Return the pilot name from a standalone <i>..</i> overview label in *raw*, or None.

    Cosmetic custom-ship-name italics (closed by ']' or trailing after ' - ') are
    excluded; only a free-standing pilot-name label is returned (inner markup stripped).
    """
    without = _COSMETIC_CLOSED_RE.sub("", raw)
    without = _COSMETIC_SUFFIX_RE.sub("", without)
    m = _ITALIC_RE.search(without)
    if not m:
        return None
    pilot = strip_eve_markup(m.group(1)).strip()
    return pilot or None


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
#  Unified structural counterparty resolver
# --------------------------------------------------------------------------- #
#
# EVE overview ship-label settings vary the *layout* of a counterparty independently
# of the message type: which tokens appear (pilot / ship / corp ticker / alliance
# ticker), their order, and whether each is wrapped in [square], <angle> (HTML-encoded
# "&lt;..&gt;") or (paren) brackets, or left bare. Rather than guess a fixed encoding,
# we tokenise the (already markup-stripped) counterparty into ordered SEGMENTS — bare
# text runs and bracket/angle/paren groups — then classify each segment STRUCTURALLY:
#
#   * a (paren) group is always the SHIP type, e.g. "...&lt;NV&gt;(Absolution)".
#   * a [square]/<angle> group is a corp/alliance TICKER iff it is short (<=5 chars
#     ignoring spaces / a trailing '.'), all-upper-case and contains no lower-case —
#     otherwise it is a PILOT name (a space, a lower-case letter, or length>5 all mark
#     a character: "[Glavior]", "&lt;Orie toori&gt;", "[Daniel BELL Carem]").
#   * bare runs are the pilot and/or ship depending on position.
#
# Positional rules then assemble (name, ship):
#   - leading bare + a pilot-group  -> ship=lead, pilot=group  ("Ship [Pilot]"/"Ship &lt;Pilot&gt;")
#   - leading bare + trailing bare   -> pilot=lead, ship=trail  (NEW "Pilot [CORP] Ship")
#   - leading bare + (paren ship)    -> pilot=lead, ship=paren  ("Pilot [CORP]&lt;ALLI&gt;(Ship)")
#   - leading bare, only tickers     -> terse ship-only, or fused (split_entity finishes)
#   - starts with a group + bare     -> ship=first group, pilot=bare  ("[Ship] Pilot [CORP]")
#
# A bare run that is fused "ShipType PilotName" with no delimiter (e.g.
# "Onyx Jennifer Hibra") cannot be split here (no SDE); we hand the whole run to `name`
# with ship=None so the SDE-aware split_entity stage peels the known ship off the front.
_GROUP_RE = re.compile(r"\[([^\[\]]*)\]|&lt;([^&]*?)&gt;|<([^<>]*)>|\(([^()]*)\)")


def _has_alnum(s: str) -> bool:
    return any(c.isalnum() for c in s)


def _looks_ticker(content: str) -> bool:
    """True if *content* is a corp/alliance ticker (short, upper-case, no pilot cues)."""
    if not _has_alnum(content) or any(c.islower() for c in content):
        return False
    return len(content.replace(" ", "").rstrip(".")) <= 5


def _clean_party_token(s: str) -> str | None:
    """Trim surrounding separator/custom-label punctuation; None if no name remains."""
    s = s.strip().strip("-+>< \t")
    return s if s and _has_alnum(s) else None


def _segments(party: str) -> list[tuple[str, str]]:
    """Tokenise into ordered ('bare'|'sq'|'ang'|'par', content) segments."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _GROUP_RE.finditer(party):
        if m.start() > pos:
            bare = party[pos:m.start()].strip()
            if bare:
                out.append(("bare", bare))
        if m.group(1) is not None:
            out.append(("sq", m.group(1).strip()))
        elif m.group(2) is not None:
            out.append(("ang", m.group(2).strip()))
        elif m.group(3) is not None:
            out.append(("ang", m.group(3).strip()))
        else:
            out.append(("par", m.group(4).strip()))
        pos = m.end()
    tail = party[pos:].strip()
    if tail:
        out.append(("bare", tail))
    return out


def _resolve_counterparty(
    party: str, raw: str = ""
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve a combat-log counterparty to (name, corp, alli, ship), overview-agnostic.

    See the module comment above for the segment-classification + positional ruleset.
    Returns name=None for a counterparty rendered ship-only / NPC (no pilot in the log).
    A fused "ShipType PilotName" bare run is returned as ``name`` with ``ship=None`` for
    the SDE-aware split_entity stage to peel.
    """
    party = (party or "").strip()
    if not party:
        return None, None, None, None

    segs = _segments(party)
    bares = [c for k, c in segs if k == "bare" and _clean_party_token(c)]
    groups = [(k, c) for k, c in segs if k in ("sq", "ang")]
    # A (paren) group is the SHIP only when its content is not itself a ticker — an
    # alliance ticker can also render in parens, e.g. "Heretic(NV)" (ship-only + alli).
    paren_ships = [c for k, c in segs if k == "par" and _has_alnum(c) and not _looks_ticker(c)]
    paren_tickers = [c for k, c in segs if k == "par" and _looks_ticker(c)]
    pilot_groups = [c for k, c in groups if _has_alnum(c) and not _looks_ticker(c)]
    tickers = [c for k, c in groups if _looks_ticker(c)] + paren_tickers
    # An angle ticker ("&lt;NV&gt;") marks the angle-FIRST overview layout where the
    # ship leads and the pilot trails ("Proteus &lt;NV&gt;[NVACA] Stephen King RDG"),
    # vs NEW ("Pilot [CORP][ALLI] Ship") where the pilot leads. A square-only two-bare
    # run with no angle is assumed NEW; the rarer single-ticker "Ship [CORP] Pilot"
    # ship-first overview is corrected in the SDE stage (split_entity swap) since the
    # pure parser cannot tell which bare is the known ship.
    has_angle_ticker = any(k == "ang" and _looks_ticker(c) for k, c in groups)
    has_angle_group = any(k == "ang" for k, c in groups)
    ship_par = paren_ships[0] if paren_ships else None
    starts_with_group = bool(segs) and segs[0][0] in ("sq", "ang", "par")

    corp = tickers[0] if tickers else None
    alli = tickers[1] if len(tickers) > 1 else None

    # A standalone <i>pilot</i> overview label in the raw line is an authoritative
    # pilot signal that strip_eve_markup fuses into the bare run ("<i>Body Cam Off</i>
    # Heretic(NV)" → "Body Cam Off Heretic(NV)"). Recover it and resolve the ship from
    # the remainder. (Cosmetic custom-ship italics, closed by ']' or after ' - ', are
    # excluded by _italic_pilot_label.)
    pilot_label = _italic_pilot_label(raw) if raw else None
    if pilot_label:
        rest = party.replace(pilot_label, "", 1).strip(" -\t")
        rest_ship = None
        if rest:
            r = _resolve_counterparty(rest)
            rest_ship = r[3] or r[0]
        return pilot_label, corp, alli, rest_ship

    name: str | None = None
    ship: str | None = None

    if not starts_with_group and bares:
        lead = _clean_party_token(bares[0])
        if pilot_groups:                       # "Ship [Pilot] - [ALLI]", "Ship &lt;Pilot&gt;"
            name = _clean_party_token(pilot_groups[0])
            ship = ship_par or lead
        elif ship_par:                         # "Pilot [CORP]&lt;ALLI&gt;(Ship)"
            name = lead
            ship = ship_par
        elif len(bares) >= 2 and has_angle_ticker:   # angle-first "Ship &lt;ALLI&gt;[CORP] Pilot"
            name = _clean_party_token(bares[-1])
            ship = lead
        elif len(bares) >= 2:                  # NEW "Pilot [CORP][ALLI] Ship"
            name = lead
            ship = _clean_party_token(bares[-1])
        elif tickers and not has_angle_group:  # terse "Ship [CORP] [ALLI]" — ship-only, no pilot
            name = None
            ship = lead
        else:                                  # bare NPC / angle-fused "Ship Pilot" → SDE peels
            name = lead
            ship = None
    else:
        # Starts with a group: "[Ship] Pilot [CORP]" or only-groups / "[Pilot]".
        non_ticker_group = next((c for k, c in groups if not _looks_ticker(c)), None)
        if bares:
            name = _clean_party_token(bares[0])
            ship = ship_par or non_ticker_group
        elif pilot_groups:                     # "[Pilot]" / "[Ship]" — SDE decides which
            name = _clean_party_token(pilot_groups[0])
            ship = ship_par
        else:                                  # ticker-only (unattributable) / paren ship
            name = None
            ship = ship_par or non_ticker_group

    return name, corp, alli, ship


# --------------------------------------------------------------------------- #
#  Damage line (from / to)
# --------------------------------------------------------------------------- #

# After stripping markup, damage lines look like:
#   "432  from FakeEnemy Bravo[.TST](Brutix Navy Issue)  - 250mm Railgun II - Grazes"
# We match before stripping (raw HTML form) for accuracy on brackets embedded in names
_DAMAGE_RE = re.compile(
    r"^(\d+)\s+(from|to)\s+([\w' \-\.]+)\[([^\]]*)\]\(([^)]+)\)\s+-\s+(.+?)\s+-\s+(\w[\w ]+\w)$"
)

# A client variant logs damage with NO "[CORP](Ship)" decoration — just the bare
# pilot name, then module and quality, e.g.
#   "319 from Kyren Fumimasa - Veles Supratidal Entropic Disintegrator - Hits"
# Tried only after the full-decoration form fails, so it never weakens the rich case.
_DAMAGE_BARE_RE = re.compile(
    r"^(\d+)\s+(from|to)\s+(.+?)\s+-\s+(.+?)\s+-\s+(\w[\w ]+\w)$"
)

# NPC / Sleeper / sentry damage carries no module at all — just name and quality,
# e.g. "27 from Awakened Sentinel - Penetrates". Anchored on the closed set of
# EVE damage qualities so it can only ever match a genuine damage line.
_DAMAGE_QUALITIES = (
    "Hits", "Penetrates", "Grazes", "Smashes", "Glances Off", "Wrecks", "Barely Scratches",
)
_DAMAGE_NOMOD_RE = re.compile(
    r"^(\d+)\s+(from|to)\s+(.+?)\s+-\s+(" + "|".join(_DAMAGE_QUALITIES) + r")$"
)

# Structure damage: a system prefix, the structure's "[CORP](StructureType)" decoration,
# and a trailing "(N deflected)" with a doubled quality, e.g.
#   "6438 to J151204 - 3D Triangle Scheme[BF-F](Fortizar) - Mjolnir Fury Cruise Missile
#    - Hits - Hits (6438 deflected)"
# The counterparty is a structure (never a character); parse it so the line is not
# dropped, recording the structure name + type. Tried last, after the pilot-damage forms.
_STRUCT_DAMAGE_RE = re.compile(
    r"^(\d+)\s+(from|to)\s+.+?\s+-\s+([^\[]+?)\[([^\]]*)\]\(([^)]+)\)\s+-\s+.+?\s+\(\d+\s+deflected\)$"
)


def _match_damage(rest: str) -> dict[str, Any] | None:
    """Match damage in/out line on the already-stripped *rest*."""
    m = _DAMAGE_RE.match(rest.strip())
    if m:
        direction: Literal["in", "out"] = "in" if m.group(2) == "from" else "out"
        # Structures render as "<system> - <StructureName>[CORP](Type)"; drop the
        # leading single-token system prefix so the name is just the structure.
        other_name = re.sub(r"^[^\s\[\]]+\s+-\s+", "", m.group(3).strip())
        return {
            "effect_type": "damage",
            "direction": direction,
            "amount": float(m.group(1)),
            "other_name": other_name,
            "other_corp_ticker": m.group(4).strip() or None,
            "other_alliance_ticker": None,
            "other_ship_name": m.group(5).strip(),
            "module_name": m.group(6).strip(),
            "quality": m.group(7).strip(),
        }
    mb = _DAMAGE_BARE_RE.match(rest.strip())
    if mb:
        direction = "in" if mb.group(2) == "from" else "out"
        return {
            "effect_type": "damage",
            "direction": direction,
            "amount": float(mb.group(1)),
            "other_name": mb.group(3).strip(),
            "other_corp_ticker": None,
            "other_alliance_ticker": None,
            "other_ship_name": None,
            "module_name": mb.group(4).strip(),
            "quality": mb.group(5).strip(),
        }
    mn = _DAMAGE_NOMOD_RE.match(rest.strip())
    if mn:
        direction = "in" if mn.group(2) == "from" else "out"
        return {
            "effect_type": "damage",
            "direction": direction,
            "amount": float(mn.group(1)),
            "other_name": mn.group(3).strip(),
            "other_corp_ticker": None,
            "other_alliance_ticker": None,
            "other_ship_name": None,
            "module_name": None,
            "quality": mn.group(4).strip(),
        }
    ms = _STRUCT_DAMAGE_RE.match(rest.strip())
    if ms:
        direction = "in" if ms.group(2) == "from" else "out"
        return {
            "effect_type": "damage",
            "direction": direction,
            "amount": float(ms.group(1)),
            "other_name": ms.group(3).strip(),
            "other_corp_ticker": ms.group(4).strip() or None,
            "other_alliance_ticker": None,
            "other_ship_name": ms.group(5).strip(),  # structure type (e.g. Fortizar)
            "module_name": None,
            "quality": "Deflected",
        }
    return None


# --------------------------------------------------------------------------- #
#  Warp disrupt / scram
# --------------------------------------------------------------------------- #

# Standard form names both parties ("from <src> to <tgt>"). A client variant logs
# only the initiator on an incoming attempt ("Warp scramble attempt from <enemy>"),
# omitting "to you" — so the " to <tgt>" tail is optional; absent ⇒ target is the
# listener ("you").
_EWAR_RE = re.compile(
    r"^Warp (disruption|scramble) attempt from (.+?)(?: to (.+))?$",
    re.DOTALL,
)


_EWAR_RAW_SPLIT_RE = re.compile(
    r"<font[^>]*>\s*from\s*</font>(.*?)<font[^>]*>\s*to[ <]", re.DOTALL
)


def _split_ewar_raw(raw: str) -> tuple[str, str]:
    """Split a raw ewar line into (source_raw, target_raw) at the inter-party 'to'.

    Lets the resolver recover a per-party standalone <i>pilot</i> label (some overviews
    log an ewar counterparty by ship with the pilot only in italics). Falls back to
    (raw, "") for the terse incoming form that omits the target.
    """
    m = _EWAR_RAW_SPLIT_RE.search(raw)
    if not m:
        return raw, ""
    return m.group(1), raw[m.end():]


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
    tgt_raw = (m.group(3) or "").strip()
    src_is_you = src_raw == "you"
    # Target omitted (terse incoming form) ⇒ the listener is the implicit target,
    # and the source is rendered as the terse "Ship [ALLI] [CORP]" (no pilot name).
    terse = m.group(3) is None
    tgt_is_you = terse or tgt_raw.rstrip("!") == "you"

    src_seg, tgt_seg = _split_ewar_raw(rest_raw)
    src_id = _resolve_counterparty(src_raw, src_seg)
    tgt_id = _resolve_counterparty(tgt_raw, tgt_seg)
    source_name: str | None = None if src_is_you else src_id[0]
    target_name: str | None = None if tgt_is_you else tgt_id[0]
    # The ship the resolver already separated from each pilot (None for "you" and for
    # fused "ShipType Pilot" runs the SDE stage must still peel).  ingest uses this to
    # decide whether source_name/target_name is already a clean pilot — see ingest_log.
    source_ship_name: str | None = None if src_is_you else src_id[3]
    target_ship_name: str | None = None if tgt_is_you else tgt_id[3]
    authoritative = src_is_you or tgt_is_you

    if src_is_you:
        direction: Literal["in", "out"] = "out"
        name, corp, alli, ship = tgt_id
    elif tgt_is_you:
        direction = "in"
        name, corp, alli, ship = src_id
    else:
        # Third-party: record the REAL initiator (source), never the log owner.
        direction = "in"
        name, corp, alli, ship = src_id

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
        "source_ship_name": source_ship_name,
        "target_ship_name": target_ship_name,
        "authoritative": authoritative,
    }


# --------------------------------------------------------------------------- #
#  Energy neutralized (neut — in or out)
# --------------------------------------------------------------------------- #

# Stripped form: "234 GJ energy neutralized Other [ALLI][CORP] Ship - Module"
# Trailing " - <module>" omitted by the terse variant (e.g.
# "168 GJ energy neutralized Leshak [MSF.] [DDOG.]"), so it is optional.
_NEUT_RE = re.compile(
    r"^(\d+)\s+GJ energy neutralized\s+(.+?)(?:\s+-\s+(.+))?$",
    re.DOTALL,
)

# Direction signal.  A neutralization line carries NO "from/to" keyword and is
# byte-identical in both directions — only the colour of the leading "<n> GJ" amount
# encodes who is the actor, exactly the scheme NOS uses (confirmed in 2.2k real logs:
# "energy drained from" is always 0xff7fffff, "energy drained to" always 0xffe57f7f).
#   * 0xff7fffff (cyan)  → OUTGOING — you neut them; the named party is the TARGET and
#                          the module is your own neutralizer.
#   * 0xffe57f7f (red)   → INCOMING — they neut you; the named party is the SOURCE and
#                          the module is the attacker's.
# Earlier code assumed neut was always outgoing (the "energy neutralized <you>" form was
# wrongly believed never logged); that mis-credited every pilot *being* neuted — e.g. a
# logi Nestor with no neut fitted — as if they were applying it.
_NEUT_INCOMING_COLOUR = "e57f7f"
_LEADING_COLOUR_RE = re.compile(r"<color=(0x[0-9a-fA-F]+)>", re.IGNORECASE)


def _neut_direction(rest_raw: str) -> Literal["in", "out"]:
    """Return neut direction from the leading amount colour; default 'out' if absent.

    The first ``<color=...>`` tag in the raw line is always the "<n> GJ" amount colour.
    """
    m = _LEADING_COLOUR_RE.search(rest_raw)
    if m and m.group(1).lower().endswith(_NEUT_INCOMING_COLOUR):
        return "in"
    return "out"


def _match_neut(rest_stripped: str, rest_raw: str = "") -> dict[str, Any] | None:
    m = _NEUT_RE.match(rest_stripped)
    if not m:
        return None
    other_part = m.group(2).strip()
    module_name = m.group(3).strip() if m.group(3) else None
    name, corp, alli, ship = _resolve_counterparty(other_part, rest_raw)
    return {
        "effect_type": "neut",
        "direction": _neut_direction(rest_raw),
        "amount": float(m.group(1)),
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": module_name,
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
    r"^([+-]?\d+)\s+GJ energy drained (from|to)\s+(.+?)(?:\s+-\s+-?\s*(.+))?$",
    re.DOTALL,
)


def _match_nos(rest_stripped: str, rest_raw: str = "") -> dict[str, Any] | None:
    """Match nosferatu (energy drained from/to) lines.

    Real-log finding: incoming NOS ("energy drained to <X>") IS logged client-side with a
    negative signed amount and OLD encoding. Outgoing NOS ("energy drained from <X>") uses
    OLD encoding for player targets and bare name for NPCs. (Incoming neutralization IS
    likewise logged — see _match_neut, which reads the amount colour for direction since
    neutralization lines carry no from/to keyword.)
    """
    m = _NOS_RE.match(rest_stripped)
    if not m:
        return None
    amount_str = m.group(1)
    drain_word = m.group(2)  # "from" or "to"
    party_part = m.group(3).strip()
    module_name = m.group(4).strip() if m.group(4) else None

    direction: Literal["in", "out"] = "out" if drain_word == "from" else "in"
    name, corp, alli, ship = _resolve_counterparty(party_part, rest_raw)

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

# Trailing " - <module>" is omitted by a terse client variant (e.g.
# "1024 remote armor repaired by Zarmazd [NV] [NVACA]"), so it is optional.
_REP_RE = re.compile(
    r"^(\d+)\s+remote (armor repaired|shield boosted) (to|by)\s+(.+?)(?:\s+-\s+-?\s*(.+))?$",
    re.DOTALL,
)


def _match_rep(rest_stripped: str, rest_raw: str = "") -> dict[str, Any] | None:
    m = _REP_RE.match(rest_stripped)
    if not m:
        return None
    amount = float(m.group(1))
    rep_kind = m.group(2)  # "armor repaired" or "shield boosted"
    direction_word = m.group(3)  # "to" or "by"
    party_part = m.group(4).strip()
    module_name = m.group(5).strip() if m.group(5) else None

    effect_type = "rep_armor" if "armor" in rep_kind else "rep_shield"
    direction: Literal["in", "out"] = "out" if direction_word == "to" else "in"

    name, corp, alli, ship = _resolve_counterparty(party_part, rest_raw)

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
    r"^(\d+)\s+remote capacitor transmitted (to|by)\s+(.+?)(?:\s+-\s+-?\s*(.+))?$",
    re.DOTALL,
)


def _match_cap(rest_stripped: str, rest_raw: str = "") -> dict[str, Any] | None:
    m = _CAP_RE.match(rest_stripped)
    if not m:
        return None
    amount = float(m.group(1))
    direction_word = m.group(2)  # "to" or "by"
    party_part = m.group(3).strip()
    module_name = m.group(4).strip() if m.group(4) else None

    direction: Literal["in", "out"] = "out" if direction_word == "to" else "in"
    name, corp, alli, ship = _resolve_counterparty(party_part, rest_raw)

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

# Classic ECM jam, logged on the (combat) channel as the listener's locks dropping.
# The jammer is rendered in the NEW encoding or the terse "Ship [ALLI] [CORP]" form.
_JAM_BROKEN_RE = re.compile(r"^Your target locks broken by (.+)$", re.DOTALL)


def _match_jam_broken(rest_stripped: str) -> dict[str, Any] | None:
    m = _JAM_BROKEN_RE.match(rest_stripped)
    if not m:
        return None
    # "<jammer party> - <module>" (module may be double-dashed); drop the trailing
    # module (the final " - <text>" carrying no bracket/ticker) so only the jammer
    # counterparty is resolved.
    party = m.group(1).strip()
    party = re.sub(r"\s+-\s+-?\s*[^][<>]*$", "", party).strip()
    name, corp, alli, ship = _resolve_counterparty(party)
    return {
        "effect_type": "jam",
        "direction": "in",
        "amount": None,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": None,
        "quality": None,
    }


# Outgoing ECM (burst) jammer: the listener breaks a target's locks, logged as
# "<victim> ... target locks broken - <module>" (standard and terse clients). The
# victim is typically a drone/fighter and is never a reliable pilot counterparty,
# so other_name is left None (the jam is counted, not attributed).
_JAM_OUT_RE = re.compile(r"^.+?\s+(?:-\s+)?target locks broken\s+-\s+(.+)$", re.DOTALL)


def _match_jam_out(rest_stripped: str) -> dict[str, Any] | None:
    m = _JAM_OUT_RE.match(rest_stripped)
    if not m:
        return None
    return {
        "effect_type": "jam",
        "direction": "out",
        "amount": None,
        "other_name": None,
        "other_corp_ticker": None,
        "other_alliance_ticker": None,
        "other_ship_name": None,
        "module_name": m.group(1).strip(),
        "quality": None,
    }


def _match_jam(tag: str, rest_stripped: str) -> dict[str, Any] | None:
    if tag != "notify":
        return None
    m = _JAM_RE.match(rest_stripped)
    if not m:
        return None
    name, corp, alli, ship = _resolve_counterparty(m.group(1).strip())
    return {
        "effect_type": "jam",
        "direction": "in",
        "amount": None,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
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
    #: Ship the parser already separated from each ewar party's pilot (transient,
    #: not persisted): None means the name may still be a fused "ShipType Pilot" run
    #: for the SDE stage to peel.  See ingest_log's source/target cleaning.
    source_ship_name: str | None = None
    target_ship_name: str | None = None
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
    "source_ship_name": None,
    "target_ship_name": None,
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
            effect = _match_neut(rest_stripped, rest_raw)
        if effect is None:
            effect = _match_nos(rest_stripped, rest_raw)
        if effect is None:
            effect = _match_rep(rest_stripped, rest_raw)
        if effect is None:
            effect = _match_cap(rest_stripped, rest_raw)
        if effect is None:
            effect = _match_jam_broken(rest_stripped)
        if effect is None:
            effect = _match_jam_out(rest_stripped)
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
        source_ship_name=effect.get("source_ship_name"),
        target_ship_name=effect.get("target_ship_name"),
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
