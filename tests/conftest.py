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
from app.fights.offbr_cache import reset_offbr_cache_for_tests
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
    reset_offbr_cache_for_tests()
    HEALTH.roster_loaded = False
    HEALTH.roster_version = 0
    HEALTH.roster_fetched_at = 0.0
    HEALTH.data_source = ""


@pytest.fixture(autouse=True)
def _isolate_from_real_environment(tmp_path_factory, monkeypatch):  # type: ignore[no-untyped-def]
    """Every test starts from safe, throwaway settings.

    Without this, ``Settings`` reads the developer's ``.env`` — so a test that
    does not set ``DB_PATH`` writes into the real ``var/db/dev.db`` and a real
    ``NV_API_TOKEN`` / ``DISCORD_BOT_TOKEN`` would reach real upstreams. Tests
    that need something else just ``monkeypatch.setenv`` over these.
    """
    root = tmp_path_factory.mktemp("runtime")
    safe = {
        "DB_PATH": str(root / "db" / "test.db"),
        "LOG_DIR": str(root / "logs"),
        "ESI_CACHE_DIR": str(root / "esi"),
        "SDE_DIR": str(root / "sde"),
        "CONFIG_PATH": str(root / "config.toml"),
        "CONFIG_LOCAL_PATH": str(root / "config.local.toml"),
        "DATA_SOURCE": "demo",
        "DEV_MODE": "0",
        "NV_TOKEN": TEST_TOKEN,
        "NV_API_TOKEN": "",
        "URL_PREFIX": "",
        "DISCORD_BOT_TOKEN": "",
        "DISCORD_FORUM_CHANNEL_ID": "",
        "DISCORD_MEMBER_CHANNEL_ID": "",
        "DISCORD_ALERT_CHANNEL_ID": "",
        "PUBLIC_BASE_URL": "",
        "BACKUP_RCLONE_REMOTE": "",
        "RESTORE_ON_START": "0",
    }
    for key, value in safe.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    yield
    get_settings.cache_clear()
    get_app_config.cache_clear()


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
