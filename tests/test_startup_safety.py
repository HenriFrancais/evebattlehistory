"""The app refuses to start in configurations that silently disable auth, the
image build cannot hide a missing SDE, and /healthz reflects real readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import CREATOR_HEADERS

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "deploy" / "Dockerfile").read_text()


def _settings(**kw):  # type: ignore[no-untyped-def]
    from app.config import Settings

    return Settings(**kw)


def test_default_or_empty_token_is_refused_outside_dev_mode() -> None:
    from app.config import ConfigError, validate_runtime_settings

    for token in ("dev-token-change-me", "", "   "):
        with pytest.raises(ConfigError, match="NV_TOKEN"):
            validate_runtime_settings(_settings(nv_token=token, dev_mode=False))


def test_dev_mode_is_refused_behind_a_url_prefix() -> None:
    from app.config import ConfigError, validate_runtime_settings

    with pytest.raises(ConfigError, match="DEV_MODE"):
        validate_runtime_settings(_settings(nv_token="real", dev_mode=True, url_prefix="/fc/br"))


def test_local_dev_and_production_configs_are_accepted() -> None:
    from app.config import validate_runtime_settings

    validate_runtime_settings(_settings(dev_mode=True, url_prefix=""))  # local dev, default token
    validate_runtime_settings(_settings(nv_token="s3cret", dev_mode=False, url_prefix="/fc/br"))


def test_app_does_not_boot_with_the_default_token(make_client) -> None:  # type: ignore[no-untyped-def]
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        make_client(NV_TOKEN="dev-token-change-me")


def test_unauthorized_response_still_carries_the_frame_csp(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/me")
    assert r.status_code == 401
    assert "frame-ancestors" in r.headers["content-security-policy"]


def test_healthz_checks_the_database_and_reports_sde(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["db"] is True
    assert body["sde_types"] == 0  # no SDE artifact in the test environment
    assert body["schema_version"] >= 1

    import app.observability.health as health

    async def broken() -> bool:
        return False

    monkeypatch.setattr(health, "_db_ok", broken)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["ok"] is False
    assert client.get("/api/me", headers=CREATOR_HEADERS).status_code == 200


def test_image_build_fails_when_the_sde_cannot_be_fetched() -> None:
    sde_run = DOCKERFILE[DOCKERFILE.index("python -m app.sde.refresh") - 200:]
    sde_run = sde_run[: sde_run.index("\n\n")]
    assert "SDE_REQUIRED" in DOCKERFILE
    # The refresh must not be chained into a blanket "|| true".
    assert "app.sde.refresh \\\n    && mkdir" not in DOCKERFILE


def test_image_installs_locked_dependencies_and_ships_config() -> None:
    assert "uv.lock" in DOCKERFILE
    assert "--frozen" in DOCKERFILE
    assert "COPY config.toml" in DOCKERFILE
