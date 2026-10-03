"""The one place UTC conversions live.

Convention: every timestamp in this app is UTC. SQLite stores datetimes as text
and drops tzinfo, so values read back from the database are NAIVE and mean UTC;
values from the API / ESI are tz-aware. These helpers move between the two
without ever interpreting a naive value in the server's local timezone.
"""

from __future__ import annotations

import datetime as dt
from typing import overload


def as_utc(ts: dt.datetime) -> dt.datetime:
    """Return *ts* as a tz-aware UTC datetime (naive ⇒ already UTC; aware ⇒ converted)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.UTC)
    return ts.astimezone(dt.UTC)


@overload
def naive_utc(ts: dt.datetime) -> dt.datetime: ...
@overload
def naive_utc(ts: None) -> None: ...
def naive_utc(ts: dt.datetime | None) -> dt.datetime | None:
    """Return *ts* as a naive datetime in UTC (the shape SQLite hands back)."""
    if ts is None or ts.tzinfo is None:
        return ts
    return ts.astimezone(dt.UTC).replace(tzinfo=None)


def epoch(ts: dt.datetime) -> int:
    """Epoch seconds for *ts*, treating a naive value as UTC."""
    return int(as_utc(ts).timestamp())
