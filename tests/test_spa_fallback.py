"""SPA history-fallback: deep client routes resolve on hard refresh / direct link."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CREATOR_HEADERS, TEST_TOKEN

PREFIX = "/fc/br"
INDEX_MARKER = "<!doctype html><title>nvbr-spa-test</title><div id=root></div>"


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_for_deep_routes(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.main as main
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models, reset_engine_for_tests

    # Fake built SPA so the mount activates without a real Vite build.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_MARKER)
    (dist / "assets" / "app.js").write_text("console.log('app')")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("URL_PREFIX", PREFIX)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = main.create_app()
    with TestClient(app) as client:
        # Deep client route (the bug): hard refresh / direct link → index.html, not 404.
        r = client.get(f"{PREFIX}/brs/f54ca020-867c-4802-a60d-a920c0ca92f6", headers=CREATOR_HEADERS)
        assert r.status_code == 200
        assert "nvbr-spa-test" in r.text

        # SPA root still works.
        assert client.get(f"{PREFIX}/", headers=CREATOR_HEADERS).status_code == 200

        # Real static asset is served as itself, not the fallback.
        ra = client.get(f"{PREFIX}/assets/app.js", headers=CREATOR_HEADERS)
        assert ra.status_code == 200 and "console.log" in ra.text

        # Unknown API path stays a JSON 404 (not swallowed by the SPA fallback).
        rapi = client.get(f"{PREFIX}/api/does-not-exist", headers=CREATOR_HEADERS)
        assert rapi.status_code == 404
        assert "nvbr-spa-test" not in rapi.text

        # Health endpoint still resolves to the router.
        assert client.get(f"{PREFIX}/healthz").status_code == 200

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()
