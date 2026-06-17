"""Shared test fixtures.

``make_client`` boots the app with env overrides and demo data so tests need no
network or real .env/config.toml. The lru_cached settings/config singletons and
the roster store are cleared on every boot.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_app_config, get_settings
from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
from app.observability.health import HEALTH
from app.roster.snapshot import reset_roster_store_for_tests

TEST_TOKEN = "test-token"

# Headers the proxy injects for a user who MAY create BRs (High Command).
CREATOR_HEADERS = {
    "Authorization": f"Bearer {TEST_TOKEN}",
    "X-User-Name": "Ra'zok",
    "X-User-Rank": "High Command",
    "X-User-Teams": "fc,logistics",
    "X-User-Main-Character-Id": "2112615087",
}

# Headers for an authenticated user who may NOT create BRs.
MEMBER_HEADERS = {
    "Authorization": f"Bearer {TEST_TOKEN}",
    "X-User-Name": "LineMember",
    "X-User-Rank": "Member",
    "X-User-Teams": "",
    "X-User-Main-Character-Id": "95000001",
}


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()
    HEALTH.roster_loaded = False
    HEALTH.roster_version = 0
    HEALTH.roster_fetched_at = 0.0
    HEALTH.data_source = ""


@pytest.fixture
def make_client(monkeypatch):
    clients: list[TestClient] = []

    def _make(**env: str) -> TestClient:
        defaults = {
            "NV_TOKEN": TEST_TOKEN,
            "DEV_MODE": "0",
            "DATA_SOURCE": "demo",
            "URL_PREFIX": "",
        }
        defaults.update(env)
        for key, value in defaults.items():
            monkeypatch.setenv(key, value)
        _clear_caches()
        from app.main import create_app

        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)
    _clear_caches()


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
async def db_session_maker(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Provide a fresh async sessionmaker backed by a temp SQLite DB.

    Sets DATA_SOURCE=demo and NV_TOKEN so callers can use get_settings() safely.
    Resets the engine singleton before and after.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    yield get_sessionmaker(settings)
    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
