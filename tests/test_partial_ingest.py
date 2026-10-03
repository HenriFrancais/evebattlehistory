"""An ingest that could not fetch everything must say so, never report a clean
"ready" with missing killmails; upstream failures are retried, then raised."""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import CREATOR_HEADERS, TEST_TOKEN
from tests.test_pipeline import _create_pending_br

_RELATED = {
    "systemName": "J1",
    "summary": {"teamA": {"kills": {"1": {"zkb": {"hash": "h1", "totalValue": 1.0}}}}},
}


async def _ingest(tmp_path, monkeypatch, fail_km: int | None = None):  # type: ignore[no-untyped-def]
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport
    from app.esi.demo import DemoEsiClient
    from app.ingest.pipeline import run_ingest

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)
    br_id = await _create_pending_br(session_maker)

    real = DemoEsiClient.fetch_killmail

    async def flaky(self, km_id: int, km_hash: str):  # type: ignore[no-untyped-def]
        if km_id == fail_km:
            raise RuntimeError("ESI 502")
        return await real(self, km_id, km_hash)

    monkeypatch.setattr(DemoEsiClient, "fetch_killmail", flaky)
    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)
    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    return br


async def test_missing_killmail_is_reported_not_hidden(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    br = await _ingest(tmp_path, monkeypatch, fail_km=103)
    assert br.status == "ready"
    assert br.km_expected == 5
    assert br.km_count == 4
    assert br.warning_text is not None
    assert "4 of 5" in br.warning_text


async def test_complete_ingest_has_no_warning(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    br = await _ingest(tmp_path, monkeypatch)
    assert (br.km_count, br.km_expected) == (5, 5)
    assert br.warning_text is None


def test_br_detail_exposes_killmail_completeness(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    db = tmp_path / "test.db"
    client = make_client(DB_PATH=str(db))
    with sqlite3.connect(db) as c:
        c.execute(
            "insert into battle_report (br_id, source, source_url, source_ref, created_by_user,"
            " status, progress_pct, created_at, km_count, km_expected, warning_text,"
            " our_isk_destroyed, our_isk_lost, fight_count)"
            " values ('b1','zkb','http://x','r','t','ready',100,'2026-01-01 00:00:00',"
            " 4, 5, 'Only 4 of 5 killmails could be fetched', 0, 0, 0)"
        )
    body = client.get("/api/brs/b1", headers=CREATOR_HEADERS).json()
    assert body["km_count"] == 4
    assert body["km_expected"] == 5
    assert "4 of 5" in body["warning_text"]


# ---------------------------------------------------------------------------
# zKillboard
# ---------------------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def no_sleep(monkeypatch):  # type: ignore[no-untyped-def]
    import asyncio

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


async def test_zkb_resolve_raises_when_zkill_keeps_failing(monkeypatch, no_sleep) -> None:  # type: ignore[no-untyped-def]
    import app.ingest.sources.zkillboard as mod
    from app.ingest.sources.base import BrUnavailable

    pre = _client(lambda r: httpx.Response(503, content=b"{}"))
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: pre)
    with pytest.raises(BrUnavailable, match="503"):
        await mod.ZkbSource().resolve("https://zkillboard.com/related/31000001/202506171500/")


async def test_zkb_retries_a_rate_limit_then_succeeds(monkeypatch, no_sleep) -> None:  # type: ignore[no-untyped-def]
    import app.ingest.sources.zkillboard as mod

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, content=b"{}")
        return httpx.Response(200, content=json.dumps(_RELATED).encode())

    pre = _client(handler)
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: pre)
    result = await mod.ZkbSource().resolve(
        "https://zkillboard.com/related/31000001/202506171500/"
    )
    assert result.refs == [(1, "h1")]
    assert len(calls) == 2
    assert no_sleep == [2.0]


async def test_window_raises_when_an_hour_cannot_be_fetched(no_sleep) -> None:  # type: ignore[no-untyped-def]
    from app.ingest.sources.base import BrUnavailable
    from app.ingest.sources.zkillboard import fetch_window_killmails

    def handler(request: httpx.Request) -> httpx.Response:
        if "202506171600" in str(request.url):
            return httpx.Response(500, content=b"{}")
        return httpx.Response(200, content=json.dumps(_RELATED).encode())

    async with _client(handler) as client:
        with pytest.raises(BrUnavailable, match="202506171600"):
            await fetch_window_killmails(
                client, 31000001,
                dt.datetime(2025, 6, 17, 15, 0, tzinfo=dt.UTC),
                dt.datetime(2025, 6, 17, 17, 0, tzinfo=dt.UTC),
            )


def test_window_longer_than_the_supported_span_is_rejected(make_client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = make_client(DB_PATH=str(tmp_path / "test.db"))
    resp = client.post(
        "/api/brs",
        json={"sources": [{
            "kind": "window", "system_id": 31000001,
            "window_start": "2026-06-10T00:00:00Z", "window_end": "2026-06-13T00:00:00Z",
        }]},
        headers=CREATOR_HEADERS,
    )
    assert resp.status_code == 400
    assert "48" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# ESI
# ---------------------------------------------------------------------------


async def test_esi_killmail_fetch_retries_a_transient_failure(tmp_path, no_sleep) -> None:  # type: ignore[no-untyped-def]
    from app.esi.client import EsiClient

    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(502, content=b"bad gateway")
        return httpx.Response(200, content=json.dumps({"killmail_id": 7}).encode())

    esi = EsiClient(cache_dir=tmp_path, user_agent="test")
    esi._http = _client(handler)
    kms = await esi.fetch_killmails([(7, "hash")])
    assert kms == [{"killmail_id": 7}]
    assert len(calls) == 3
