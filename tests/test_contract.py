"""Phase 0: the NV Tools deployment contract + identity/permission gating."""

from __future__ import annotations

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS


def test_healthz_open_and_carries_csp(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.headers["content-security-policy"].startswith("frame-ancestors")


def test_no_bearer_is_unauthorized(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_wrong_bearer_is_unauthorized(client):
    resp = client.get("/api/me", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_me_reflects_identity_and_create_permission(client):
    resp = client.get("/api/me", headers=CREATOR_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_name"] == "Ra'zok"
    assert body["user_rank"] == "High Command"
    assert "fc" in body["user_teams"]
    assert body["can_create_br"] is True


def test_member_cannot_create_br(client):
    resp = client.get("/api/me", headers=MEMBER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["can_create_br"] is False


def test_csp_on_authenticated_response(client):
    resp = client.get("/api/me", headers=CREATOR_HEADERS)
    assert resp.headers["content-security-policy"].startswith("frame-ancestors")
    assert "x-frame-options" not in {k.lower() for k in resp.headers}


def test_dev_mode_injects_identity(make_client):
    client = make_client(DEV_MODE="1", DEV_USER_RANK="CEO", DEV_USER_TEAMS="fc")
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["can_create_br"] is True


def test_url_prefix_mounts_routes(make_client):
    client = make_client(URL_PREFIX="/br")
    assert client.get("/br/healthz").status_code == 200
    assert client.get("/br/api/me", headers=CREATOR_HEADERS).status_code == 200
    # Unprefixed path should not resolve to the app routes.
    assert client.get("/healthz").status_code in (401, 404)
