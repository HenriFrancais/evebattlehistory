"""Pure parser for EVE fleet-broadcast logs.

Fleet-broadcast logs are a different grammar from combat gamelogs (see parse.py):
lines are plain text (no markup) and carry a time-of-day only, e.g.::

    00:24:17 - Target ZaphodBeebelbrox (Nighthawk)
    00:04:26 - Achilles Wareson needs capacitor (Nighthawk)
    23:38:28 - Repair Achilles Wareson (Nighthawk)

Three properties drive the design:

* **Reverse-chronological** — the newest line is first; the file walks backwards
  in time and crosses midnight (01:05 -> 00:xx -> 23:xx of the prior day -> ...).
* **Dateless** — only HH:MM:SS is present.  Absolute UTC dates are reconstructed
  by :func:`reconstruct_dates` against an anchor date chosen from the associated
  battle report's fight window (broadcast times are EVE/UTC and align to the
  gamelogs to the second, verified empirically).
* **Noisy** — most lines are fleet admin (join/leave/role/boss), "is in position",
  or loot.  Only target calls and rep/repair requests are kept; everything else is
  dropped.

Like parse.py these functions are pure (no I/O, never raise): callers feed text and
get structured output plus quality stats.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

# Every meaningful broadcast line is "HH:MM:SS - <body>".  Loot lines ("HH:MM:SS
# <pilot> has looted ...") deliberately lack the " - " separator, so anchoring on
# it already excludes them.
_PREFIX_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) - (?P<body>.+)$")

# Bodies we keep.  Anchored at end on " (<ship>)".  Ship names never contain
# parentheses, so [^()]+ is safe; the non-greedy subject absorbs spaces/handles.
_TARGET_RE = re.compile(r"^Target (?P<subject>.+?) \((?P<ship>[^()]+)\)$")
_NEEDS_RE = re.compile(
    r"^(?P<subject>.+?) needs (?P<res>shield|armor|capacitor) \((?P<ship>[^()]+)\)$"
)
_REPAIR_RE = re.compile(r"^Repair (?P<subject>.+?) \((?P<ship>[^()]+)\)$")

_NEEDS_KIND = {
    "shield": "needs_shield",
    "armor": "needs_armor",
    "capacitor": "needs_capacitor",
}


@dataclass(frozen=True)
class ParsedBroadcast:
    """One meaningful broadcast line, in original (reverse-chronological) order."""

    seq: int  # index among kept broadcasts, 0 = newest
    tod: dt.time  # time-of-day only; date reconstructed separately
    kind: str  # target | needs_shield | needs_armor | needs_capacitor | repair
    subject_name: str
    subject_ship: str | None
    raw: str


@dataclass
class ParsedBroadcastLog:
    broadcasts: list[ParsedBroadcast]
    stats: dict[str, int]


def _match_body(body: str) -> tuple[str, str, str | None] | None:
    """Return (kind, subject_name, subject_ship) for a kept body, else None."""
    m = _TARGET_RE.match(body)
    if m:
        return "target", m.group("subject").strip(), m.group("ship").strip()
    m = _NEEDS_RE.match(body)
    if m:
        return (
            _NEEDS_KIND[m.group("res")],
            m.group("subject").strip(),
            m.group("ship").strip(),
        )
    m = _REPAIR_RE.match(body)
    if m:
        return "repair", m.group("subject").strip(), m.group("ship").strip()
    return None


def parse_broadcast(text: str) -> ParsedBroadcastLog:
    """Parse a fleet-broadcast log, preserving the original reverse-chron order.

    Non-timestamped lines and admin/loot/position noise are dropped.  ``stats``
    reports total lines, kept broadcasts, and per-kind counts for observability.
    """
    broadcasts: list[ParsedBroadcast] = []
    stats: dict[str, int] = {"lines": 0, "matched": 0, "noise": 0}
    seq = 0
    for line in text.splitlines():
        line = line.rstrip("\r\n").strip()
        if not line:
            continue
        stats["lines"] += 1
        m = _PREFIX_RE.match(line)
        if not m:
            stats["noise"] += 1
            continue
        parsed = _match_body(m.group("body"))
        if parsed is None:
            stats["noise"] += 1
            continue
        kind, subject_name, subject_ship = parsed
        tod = dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        broadcasts.append(
            ParsedBroadcast(
                seq=seq,
                tod=tod,
                kind=kind,
                subject_name=subject_name,
                subject_ship=subject_ship,
                raw=line,
            )
        )
        stats["matched"] += 1
        stats[kind] = stats.get(kind, 0) + 1
        seq += 1
    return ParsedBroadcastLog(broadcasts=broadcasts, stats=stats)


def choose_anchor_date(newest_tod: dt.time, fight_end_utc: dt.datetime) -> dt.date:
    """Pick the UTC calendar date for the newest (first) broadcast line.

    The newest line is closest in time to the end of the fight.  Try the day
    before / of / after the fight end and take whichever places ``newest_tod``
    nearest to ``fight_end_utc``.  ``fight_end_utc`` must be a naive UTC datetime
    (as fights are stored); a tz-aware value is coerced to naive.
    """
    if fight_end_utc.tzinfo is not None:
        fight_end_utc = fight_end_utc.replace(tzinfo=None)
    base = fight_end_utc.date()
    candidates = [base - dt.timedelta(days=1), base, base + dt.timedelta(days=1)]
    return min(
        candidates,
        key=lambda d: abs(
            (dt.datetime.combine(d, newest_tod) - fight_end_utc).total_seconds()
        ),
    )


def reconstruct_dates(
    broadcasts: list[ParsedBroadcast], anchor_date: dt.date
) -> list[dt.datetime]:
    """Assign an absolute naive-UTC datetime to each broadcast (original order).

    ``broadcasts`` is newest-first.  Walking backwards in time, whenever a line's
    time-of-day is *greater* than the previous line's we have crossed midnight
    going backward, so the calendar day decrements.  The first (newest) line takes
    ``anchor_date``.
    """
    result: list[dt.datetime] = []
    if not broadcasts:
        return result
    day = anchor_date
    prev = broadcasts[0].tod
    for b in broadcasts:
        if b.tod > prev:
            day = day - dt.timedelta(days=1)
        result.append(dt.datetime.combine(day, b.tod))
        prev = b.tod
    return result
