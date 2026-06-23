"""Tests for the zKillboard resolver (real-mode /api/related/ endpoint).

All tests are fully offline — no network calls are made.  httpx.MockTransport
is used to intercept HTTP requests and return pre-baked responses.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zkb" / "related_sample.json"
_SAMPLE: dict = json.loads(_FIXTURE_PATH.read_text())

_SYSTEM_ID = 30004759
_DT_STR = "202506171500"
_RELATED_URL = f"https://zkillboard.com/api/related/{_SYSTEM_ID}/{_DT_STR}/"

_EXPECTED_REFS = {
    (111000001, "aaabbbccc111000001hash"),
    (111000002, "aaabbbccc111000002hash"),
    (111000003, "aaabbbccc111000003hash"),
}


def _make_transport(status: int = 200, body: object = None) -> httpx.MockTransport:
    """Return a MockTransport that responds with *body* JSON for any request."""
    payload = json.dumps(body if body is not None else _SAMPLE).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# ZkbSource.resolve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_extracts_both_teams(monkeypatch: pytest.MonkeyPatch):
    """resolve() returns refs from teamA AND teamB (3 total), sets title."""
    # Pre-build the client before patching so it uses the real constructor.
    pre_built = httpx.AsyncClient(transport=_make_transport())

    import app.ingest.sources.zkillboard as _mod

    monkeypatch.setattr(_mod.httpx, "AsyncClient", lambda **kwargs: pre_built)

    source = _mod.ZkbSource()
    result = await source.resolve(
        f"https://zkillboard.com/related/{_SYSTEM_ID}/{_DT_STR}/"
    )

    assert set(result.refs) == _EXPECTED_REFS
    assert result.source == "zkb"
    assert result.source_ref == f"{_SYSTEM_ID}/{_DT_STR}"
    assert result.title is not None
    assert "J123456" in result.title


@pytest.mark.asyncio
async def test_resolve_non200_returns_empty_refs(monkeypatch: pytest.MonkeyPatch):
    """resolve() returns empty refs (no crash) on non-200 response."""
    pre_built = httpx.AsyncClient(transport=_make_transport(status=503, body={}))

    import app.ingest.sources.zkillboard as _mod

    monkeypatch.setattr(_mod.httpx, "AsyncClient", lambda **kwargs: pre_built)

    source = _mod.ZkbSource()
    result = await source.resolve(
        f"https://zkillboard.com/related/{_SYSTEM_ID}/{_DT_STR}/"
    )

    assert result.refs == []


@pytest.mark.asyncio
async def test_resolve_missing_summary_returns_empty(monkeypatch: pytest.MonkeyPatch):
    """resolve() returns empty refs when response has no 'summary' key."""
    pre_built = httpx.AsyncClient(
        transport=_make_transport(body={"systemID": _SYSTEM_ID})
    )

    import app.ingest.sources.zkillboard as _mod

    monkeypatch.setattr(_mod.httpx, "AsyncClient", lambda **kwargs: pre_built)

    source = _mod.ZkbSource()
    result = await source.resolve(
        f"https://zkillboard.com/related/{_SYSTEM_ID}/{_DT_STR}/"
    )

    assert result.refs == []


# ---------------------------------------------------------------------------
# fetch_window_killmails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_window_killmails_extracts_both_teams():
    """fetch_window_killmails returns refs from teamA + teamB."""
    import datetime as dt

    from app.ingest.sources.zkillboard import fetch_window_killmails

    transport = _make_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        refs, _values = await fetch_window_killmails(
            client,
            _SYSTEM_ID,
            dt.datetime(2025, 6, 17, 15, 0, tzinfo=dt.UTC),
            dt.datetime(2025, 6, 17, 17, 0, tzinfo=dt.UTC),
        )

    assert set(refs) == _EXPECTED_REFS


@pytest.mark.asyncio
async def test_fetch_window_killmails_non200_empty():
    """fetch_window_killmails returns [] on non-200."""
    import datetime as dt

    from app.ingest.sources.zkillboard import fetch_window_killmails

    transport = _make_transport(status=404, body={})
    async with httpx.AsyncClient(transport=transport) as client:
        refs, _values = await fetch_window_killmails(
            client,
            _SYSTEM_ID,
            dt.datetime(2025, 6, 17, 15, 0, tzinfo=dt.UTC),
            dt.datetime(2025, 6, 17, 17, 0, tzinfo=dt.UTC),
        )

    assert refs == []


def _dttm(ms: int) -> dict:
    """Build a zKB-style dttm object from a unix-millis timestamp."""
    return {"$date": {"$numberLong": str(ms)}}


def _kill(km_id: int, hash_: str, when_ms: int | None) -> dict:
    obj: dict = {"zkb": {"hash": hash_, "totalValue": 1.0}}
    if when_ms is not None:
        obj["dttm"] = _dttm(when_ms)
    return obj


@pytest.mark.asyncio
async def test_fetch_window_killmails_queries_all_hourly_anchors():
    """A multi-hour window queries every hourly anchor and unions the kills.

    zKB /related/ buckets kills into ~1-hour battles, so a single anchor at
    window_start misses kills later in the window. Regression for the
    'middle systems resolve to 0 kills' bug.
    """
    import datetime as dt

    from app.ingest.sources.zkillboard import fetch_window_killmails

    # Each hourly anchor returns a DIFFERENT kill. window_start has none.
    ts_2000 = 1739995200_000  # 2025-02-19 20:00:00Z
    ts_2100 = 1739998800_000  # 2025-02-19 21:00:00Z
    by_anchor = {
        "202502191900": {"summary": {"teamA": {"kills": {}}, "teamB": {"kills": {}}}},
        "202502192000": {
            "summary": {
                "teamA": {"kills": {"500001": _kill(500001, "h1", ts_2000)}},
                "teamB": {"kills": {}},
            }
        },
        "202502192100": {
            "summary": {
                "teamA": {"kills": {"500002": _kill(500002, "h2", ts_2100)}},
                "teamB": {"kills": {}},
            }
        },
    }
    seen_anchors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        anchor = request.url.path.rstrip("/").split("/")[-1]
        seen_anchors.append(anchor)
        return httpx.Response(200, json=by_anchor.get(anchor, {"summary": {}}))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        refs, _values = await fetch_window_killmails(
            client,
            31002285,
            dt.datetime(2025, 2, 19, 19, 0, tzinfo=dt.UTC),
            dt.datetime(2025, 2, 19, 21, 0, tzinfo=dt.UTC),
        )

    # All three hourly anchors queried, and both later kills captured.
    assert "202502191900" in seen_anchors
    assert "202502192000" in seen_anchors
    assert "202502192100" in seen_anchors
    assert set(refs) == {(500001, "h1"), (500002, "h2")}


@pytest.mark.asyncio
async def test_fetch_window_killmails_filters_by_kill_time():
    """Kills whose dttm falls outside [window_start, window_end] are dropped.

    zKB's hourly buckets bleed past the window edges; the precise window must
    still be honoured.
    """
    import datetime as dt

    from app.ingest.sources.zkillboard import fetch_window_killmails

    in_window = 1739995200_000   # 2025-02-19 20:00:00Z (inside)
    out_window = 1740002400_000  # 2025-02-19 22:00:00Z (after window_end 21:00)
    payload = {
        "summary": {
            "teamA": {
                "kills": {
                    "600001": _kill(600001, "keep", in_window),
                    "600002": _kill(600002, "drop", out_window),
                }
            },
            "teamB": {"kills": {}},
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        refs, _values = await fetch_window_killmails(
            client,
            31002285,
            dt.datetime(2025, 2, 19, 20, 0, tzinfo=dt.UTC),
            dt.datetime(2025, 2, 19, 21, 0, tzinfo=dt.UTC),
        )

    assert set(refs) == {(600001, "keep")}


# ---------------------------------------------------------------------------
# _extract_refs_from_related - unit tests for defensiveness
# ---------------------------------------------------------------------------


def test_extract_skips_kill_missing_hash():
    """A kill entry without zkb.hash is silently skipped."""
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    data = {
        "summary": {
            "teamA": {
                "kills": {
                    "200001": {"zkb": {"hash": "goodhash"}},
                    "200002": {"zkb": {}},           # missing hash
                    "200003": {"other": "stuff"},     # missing zkb entirely
                    "200004": "not-a-dict",           # corrupt entry
                }
            },
            "teamB": {"kills": {}},
        }
    }
    refs, _values = _extract_refs_from_related(data)
    assert refs == [(200001, "goodhash")]


def test_extract_deduplicates_kill_id():
    """If the same killID appears in both teamA and teamB, it's included once."""
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    data = {
        "summary": {
            "teamA": {"kills": {"300001": {"zkb": {"hash": "hashA"}}}},
            "teamB": {"kills": {"300001": {"zkb": {"hash": "hashB"}}}},
        }
    }
    refs, _values = _extract_refs_from_related(data)
    assert len(refs) == 1
    assert refs[0][0] == 300001


def test_extract_non_dict_input():
    """Non-dict root → empty list, no crash."""
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    assert _extract_refs_from_related([]) == ([], {})
    assert _extract_refs_from_related(None) == ([], {})
    assert _extract_refs_from_related("bad") == ([], {})


def test_extract_missing_summary():
    """Missing summary key → empty list."""
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    assert _extract_refs_from_related({"systemID": 1}) == ([], {})


def test_extract_missing_team():
    """Missing teamA/teamB → skipped gracefully."""
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    data = {
        "summary": {
            "teamA": {"kills": {"400001": {"zkb": {"hash": "h1"}}}},
            # teamB absent
        }
    }
    refs, _values = _extract_refs_from_related(data)
    assert refs == [(400001, "h1")]
