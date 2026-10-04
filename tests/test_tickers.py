"""Corporation and alliance tickers are fetched from ESI and stored."""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select

from app.db.models import Alliance, Corporation

NOW = dt.datetime(2026, 1, 1)


def _esi(tmp_path, handler):  # type: ignore[no-untyped-def]
    from app.esi.client import EsiClient

    esi = EsiClient(cache_dir=tmp_path, user_agent="test")
    esi._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return esi


def _handler(calls: list[str]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/corporations/10/"):
            return httpx.Response(200, json={"name": "Corp Ten", "ticker": "TEN"})
        if path.endswith("/alliances/20/"):
            return httpx.Response(200, json={"name": "Alliance Twenty", "ticker": "TWNTY"})
        return httpx.Response(404, json={"error": "not found"})

    return handler


async def _seed(session) -> None:  # type: ignore[no-untyped-def]
    session.add(Alliance(alliance_id=20, name="Alliance Twenty", last_seen_at=NOW))
    session.add(Alliance(alliance_id=21, name="Known", ticker="KNOWN", last_seen_at=NOW))
    await session.flush()
    session.add(Corporation(corporation_id=10, name="Corp Ten", last_seen_at=NOW))
    session.add(Corporation(corporation_id=11, name="Gone Corp", last_seen_at=NOW))
    await session.commit()


async def test_missing_tickers_are_fetched_and_stored(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.tickers import fill_missing_tickers

    calls: list[str] = []
    esi = _esi(tmp_path, _handler(calls))
    async with db_session_maker() as session:
        await _seed(session)
        filled = await fill_missing_tickers(session, esi)
        await session.commit()
    async with db_session_maker() as session:
        corps = {c.corporation_id: c.ticker for c in (await session.execute(select(Corporation))).scalars()}
        allis = {a.alliance_id: a.ticker for a in (await session.execute(select(Alliance))).scalars()}

    assert filled == 2
    # The corp ESI does not know stays null and does not stop the others.
    assert corps == {10: "TEN", 11: None}
    assert allis == {20: "TWNTY", 21: "KNOWN"}
    # An entity that already has a ticker is never refetched.
    assert not any(p.endswith("/alliances/21/") for p in calls)


async def test_fill_can_be_limited_to_given_ids(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.tickers import fill_missing_tickers

    calls: list[str] = []
    esi = _esi(tmp_path, _handler(calls))
    async with db_session_maker() as session:
        await _seed(session)
        filled = await fill_missing_tickers(session, esi, corp_ids={10}, alliance_ids=set())
        await session.commit()

    assert filled == 1
    assert [p for p in calls if "/alliances/" in p] == []
    assert calls == ["/latest/corporations/10/"]


async def test_upstream_failure_never_raises(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.tickers import fill_missing_tickers

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    esi = _esi(tmp_path, boom)
    async with db_session_maker() as session:
        await _seed(session)
        assert await fill_missing_tickers(session, esi) == 0
