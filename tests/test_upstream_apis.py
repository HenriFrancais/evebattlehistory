"""Upstream etiquette + robustness: ESI POSTs honour the error limit, zKillboard
gets an identifying User-Agent, and a failed name lookup never erases a name."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Alliance, Character, Corporation


@pytest.fixture
def no_sleep(monkeypatch):  # type: ignore[no-untyped-def]
    import asyncio

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


def _esi(tmp_path, handler):  # type: ignore[no-untyped-def]
    from app.esi.client import EsiClient

    esi = EsiClient(cache_dir=tmp_path, user_agent="test")
    esi._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return esi


async def test_esi_post_waits_out_the_error_limit_and_retries(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(420, headers={"X-ESI-Error-Limit-Reset": "7"}, content=b"{}")
        return httpx.Response(200, content=json.dumps(
            [{"id": 5, "name": "Five", "category": "character"}]).encode())

    names = await _esi(tmp_path, handler).resolve_names([5])
    assert names == {5: {"name": "Five", "category": "character"}}
    assert calls == ["POST", "POST"]
    assert 7.0 in no_sleep


async def test_esi_slows_down_when_the_error_budget_is_nearly_spent(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-ESI-Error-Limit-Remain": "3", "X-ESI-Error-Limit-Reset": "12"},
            content=json.dumps({"characters": [{"id": 9, "name": "Nine"}]}).encode(),
        )

    out = await _esi(tmp_path, handler).resolve_ids(["Nine"])
    assert out == {"Nine": 9}
    assert no_sleep == [12.0]


async def test_zkillboard_requests_identify_the_app(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.ingest.sources.zkillboard as mod
    from app.config import Settings
    from app.ingest.sources.factory import get_source

    seen: dict[str, object] = {}
    real = httpx.AsyncClient

    def spy(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return real(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b'{"summary": {}}')), **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", spy)
    settings = Settings(data_source="real", esi_user_agent="nv-br/1.0 (ops@example.org)")
    source = get_source("https://zkillboard.com/related/31000001/202506171500/", settings)
    await source.resolve("https://zkillboard.com/related/31000001/202506171500/")
    assert seen["headers"]["User-Agent"] == "nv-br/1.0 (ops@example.org)"  # type: ignore[index]


def test_default_user_agent_names_a_contact() -> None:
    from app.config import Settings

    ua = Settings().esi_user_agent
    assert "contact admin" not in ua
    assert "github.com" in ua or "@" in ua


def _km(km_id: int) -> dict[str, object]:
    return {
        "killmail_id": km_id, "killmail_time": "2026-06-01T00:00:00Z", "solar_system_id": 1,
        "victim": {"character_id": 10, "corporation_id": 20, "alliance_id": 30,
                   "ship_type_id": 40, "damage_taken": 1, "items": []},
        "attackers": [],
    }


async def test_failed_name_lookup_does_not_erase_known_names(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.persist import persist_killmails

    names = {
        10: {"name": "Pilot", "category": "character"},
        20: {"name": "Corp", "category": "corporation"},
        30: {"name": "Alliance", "category": "alliance"},
    }
    async with db_session_maker() as session:
        await persist_killmails(session, [_km(1)], names)
        await persist_killmails(session, [_km(2)], {})  # ESI names down this time
        await session.commit()
        assert (await session.execute(select(Character.name))).scalar_one() == "Pilot"
        assert (await session.execute(select(Corporation.name))).scalar_one() == "Corp"
        assert (await session.execute(select(Alliance.name))).scalar_one() == "Alliance"


async def test_a_new_name_still_replaces_the_old_one(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.persist import persist_killmails

    async with db_session_maker() as session:
        await persist_killmails(session, [_km(1)], {10: {"name": "Old", "category": "character"}})
        await persist_killmails(session, [_km(2)], {10: {"name": "New", "category": "character"}})
        await session.commit()
        assert (await session.execute(select(Character.name))).scalar_one() == "New"
        seen = (await session.execute(select(Character.last_seen_at))).scalar_one()
        assert isinstance(seen, dt.datetime)


def _ids_handler(calls: list[int], *, bad: str | None = None, flaky: list[int] | None = None):  # type: ignore[no-untyped-def]
    """Behaves like ESI /universe/ids/: at most 500 names per call, 400 for a batch
    holding an unacceptable name, and (optionally) one 504 for the first N calls."""
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        names = json.loads(request.content)
        calls.append(len(names))
        if flaky:
            return httpx.Response(flaky.pop(0), json={"error": "timeout"})
        if len(names) > 500:
            return httpx.Response(400, json={"error": "too many names"})
        if bad is not None and bad in names:
            return httpx.Response(400, json={"error": "bad name"})
        return httpx.Response(
            200, json={"characters": [{"id": int(n[1:]), "name": n} for n in names]}
        )

    return handler


async def test_resolve_ids_never_sends_more_than_500_names(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []
    names = [f"P{i}" for i in range(1, 1347)]  # the production backfill: 1346 names
    out = await _esi(tmp_path, _ids_handler(calls)).resolve_ids(names)
    assert len(out) == 1346 and out["P1346"] == 1346
    assert max(calls) <= 500


async def test_one_unacceptable_name_does_not_lose_the_rest_of_its_batch(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []
    names = [f"P{i}" for i in range(1, 41)]
    out = await _esi(tmp_path, _ids_handler(calls, bad="P7")).resolve_ids(names)
    assert set(out) == set(names) - {"P7"}


async def test_a_gateway_timeout_is_retried_in_smaller_batches(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []
    names = [f"P{i}" for i in range(1, 347)]
    out = await _esi(tmp_path, _ids_handler(calls, flaky=[504])).resolve_ids(names)
    assert len(out) == 346
    assert calls[0] == 346 and calls[1:] == [173, 173]
