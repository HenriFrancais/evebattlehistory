"""UTC handling lives in one module; API datetimes are normalised to UTC at the
schema boundary so SQLite (which drops tzinfo) always stores UTC wall-clock."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path

from tests.conftest import CREATOR_HEADERS

PLUS2 = dt.timezone(dt.timedelta(hours=2))
APP = Path(__file__).resolve().parent.parent / "app"


def test_as_utc_attaches_or_converts() -> None:
    from app.timeutil import as_utc

    naive = dt.datetime(2026, 6, 10, 20, 0)
    assert as_utc(naive) == dt.datetime(2026, 6, 10, 20, 0, tzinfo=dt.UTC)
    aware = dt.datetime(2026, 6, 10, 20, 0, tzinfo=PLUS2)
    assert as_utc(aware) == dt.datetime(2026, 6, 10, 18, 0, tzinfo=dt.UTC)
    assert as_utc(aware).utcoffset() == dt.timedelta(0)


def test_naive_utc_and_epoch() -> None:
    from app.timeutil import epoch, naive_utc

    aware = dt.datetime(2026, 6, 10, 20, 0, tzinfo=PLUS2)
    assert naive_utc(aware) == dt.datetime(2026, 6, 10, 18, 0)
    assert naive_utc(None) is None
    assert naive_utc(dt.datetime(2026, 6, 10, 18, 0)) == dt.datetime(2026, 6, 10, 18, 0)
    assert epoch(dt.datetime(1970, 1, 1, 0, 1)) == 60
    assert epoch(aware) == epoch(dt.datetime(2026, 6, 10, 18, 0))


def test_window_source_datetimes_are_normalised_to_utc() -> None:
    from app.api.schemas import BrSourceIn

    src = BrSourceIn(
        kind="window", system_id=1,
        window_start="2026-06-10T20:00:00+02:00",  # type: ignore[arg-type]
        window_end="2026-06-10T21:00:00",  # type: ignore[arg-type]  naive ⇒ UTC
    )
    assert src.window_start == dt.datetime(2026, 6, 10, 18, 0, tzinfo=dt.UTC)
    assert src.window_start.utcoffset() == dt.timedelta(0)  # type: ignore[union-attr]
    assert src.window_end == dt.datetime(2026, 6, 10, 21, 0, tzinfo=dt.UTC)


def test_offset_window_is_stored_as_utc_wall_clock(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = tmp_path / "t.db"
    client = make_client(DB_PATH=str(db))
    r = client.post("/api/brs", headers=CREATOR_HEADERS, json={"sources": [{
        "kind": "window", "system_id": 31000001,
        "window_start": "2026-06-10T20:00:00+02:00", "window_end": "2026-06-10T22:00:00+02:00",
    }]})
    assert r.status_code == 202, r.text
    with sqlite3.connect(db) as c:
        start, end = c.execute("select window_start, window_end from br_source").fetchone()
    assert start.startswith("2026-06-10 18:00:00")
    assert end.startswith("2026-06-10 20:00:00")


def test_no_module_keeps_a_private_copy_of_the_utc_helpers() -> None:
    pattern = re.compile(r"^def (_as_utc|_epoch|_as_naive_utc|_to_naive_utc|_naive_utc)\(", re.M)
    offenders = [
        str(p.relative_to(APP)) for p in APP.rglob("*.py")
        if p.name != "timeutil.py" and pattern.search(p.read_text())
    ]
    assert offenders == []
