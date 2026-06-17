"""TDD tests for E2: per-character access control + DEV_MODE impersonation.

Scenarios verified:
- can_view_character: Member sees own char (95000001) → True
- can_view_character: Member sees other char (2112615087) → False
- can_view_character: FC/HC sees any char → True
- timeline endpoint: MEMBER_HEADERS + own char → 200
- timeline endpoint: MEMBER_HEADERS + other char → 403
- timeline endpoint: CREATOR_HEADERS + any char → 200
- events endpoint: same 200/403 rules
- impersonation ON: X-Impersonate-User=LineMember, real=HC:
    - viewing 2112615087 (not theirs) → 403
    - viewing 95000001 (theirs) → 200
    - /api/me reflects LineMember + can_create_br=false
- impersonation OFF (dev_mode=false): header IGNORED → real identity governs
- GET /api/roster/users: returns demo roster users sorted by user_name
- GET /api/me: includes impersonation_available field
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN
from tests.test_association import _insert_fight

# Character IDs from the demo roster / conftest headers
RAZOK_CHAR = 2112615087   # Ra'zok's main character (HC)
MEMBER_CHAR = 95000001    # LineMember's character

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
FIGHT_END = dt.datetime(2026, 6, 10, 20, 30, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Unit tests: can_view_character
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_view_character_member_own(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Member can view their own character."""
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)

    from app.api.access import can_view_character
    from app.api.auth import CurrentUser
    from app.config import get_settings
    from app.roster.snapshot import reset_roster_store_for_tests

    get_settings.cache_clear()
    reset_roster_store_for_tests()
    settings = get_settings()
    member = CurrentUser(user_name="LineMember", rank="Member", main_character_id="95000001")
    result = await can_view_character(member, MEMBER_CHAR, settings)
    reset_roster_store_for_tests()
    assert result is True


@pytest.mark.asyncio
async def test_can_view_character_member_other(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Member cannot view another user's character."""
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)

    from app.api.access import can_view_character
    from app.api.auth import CurrentUser
    from app.config import get_settings
    from app.roster.snapshot import reset_roster_store_for_tests

    get_settings.cache_clear()
    reset_roster_store_for_tests()
    settings = get_settings()
    member = CurrentUser(user_name="LineMember", rank="Member", main_character_id="95000001")
    result = await can_view_character(member, RAZOK_CHAR, settings)
    reset_roster_store_for_tests()
    assert result is False


@pytest.mark.asyncio
async def test_can_view_character_hc_any(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """FC/HC can view any character (short-circuits before roster lookup)."""
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)

    from app.api.access import can_view_character
    from app.api.auth import CurrentUser
    from app.config import get_settings
    from app.roster.snapshot import reset_roster_store_for_tests

    get_settings.cache_clear()
    reset_roster_store_for_tests()
    settings = get_settings()
    hc = CurrentUser(user_name="Ra'zok", rank="High Command", main_character_id="2112615087")
    assert await can_view_character(hc, RAZOK_CHAR, settings) is True
    assert await can_view_character(hc, MEMBER_CHAR, settings) is True
    reset_roster_store_for_tests()


# ---------------------------------------------------------------------------
# Helpers: insert a minimal BR + fight for API endpoint tests
# ---------------------------------------------------------------------------


async def _setup_br(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Create a temp DB with one BR and fight; return br_id."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport, BrFight
    from app.roster.snapshot import reset_roster_store_for_tests

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)

    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()

    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    async with session_maker() as session:
        # Use _insert_fight which also inserts the required SolarSystem row.
        fight_id = await _insert_fight(
            session,
            victim_char_id=MEMBER_CHAR,
            attacker_char_id=RAZOK_CHAR,
            started_at=FIGHT_START,
            ended_at=FIGHT_END,
        )

        br_id = str(uuid.uuid4())
        session.add(BattleReport(
            br_id=br_id,
            source="demo",
            source_url="http://x",
            source_ref="ref",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=dt.datetime.now(dt.UTC),
        ))
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
        await session.commit()

    return br_id


# ---------------------------------------------------------------------------
# API endpoint tests: timeline + events 200/403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_member_own_char_200(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """MEMBER_HEADERS viewing their own character → 200."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{MEMBER_CHAR}/timeline",
            headers=MEMBER_HEADERS,
        )
    assert resp.status_code == 200, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_timeline_member_other_char_403(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """MEMBER_HEADERS viewing a character that is NOT theirs → 403."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/timeline",
            headers=MEMBER_HEADERS,
        )
    assert resp.status_code == 403, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_timeline_hc_any_char_200(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CREATOR_HEADERS (HC) viewing any character → 200."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        resp1 = client.get(
            f"/api/brs/{br_id}/characters/{MEMBER_CHAR}/timeline",
            headers=CREATOR_HEADERS,
        )
        resp2 = client.get(
            f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/timeline",
            headers=CREATOR_HEADERS,
        )
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_events_member_own_char_200(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """MEMBER_HEADERS → events for own char → 200."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    t_from = int(FIGHT_START.timestamp())
    t_to = int(FIGHT_END.timestamp())
    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{MEMBER_CHAR}/events?from={t_from}&to={t_to}",
            headers=MEMBER_HEADERS,
        )
    assert resp.status_code == 200, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_events_member_other_char_403(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """MEMBER_HEADERS → events for other char → 403."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    t_from = int(FIGHT_START.timestamp())
    t_to = int(FIGHT_END.timestamp())
    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/events?from={t_from}&to={t_to}",
            headers=MEMBER_HEADERS,
        )
    assert resp.status_code == 403, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


# ---------------------------------------------------------------------------
# Impersonation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impersonation_dev_on_view_own_200(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DEV_MODE on: impersonate LineMember → viewing LineMember's char → 200."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "1")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    # Real identity is HC (from CREATOR_HEADERS), impersonating LineMember
    impersonating_headers = {**CREATOR_HEADERS, "X-Impersonate-User": "LineMember"}

    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{MEMBER_CHAR}/timeline",
            headers=impersonating_headers,
        )
    assert resp.status_code == 200, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_impersonation_dev_on_view_other_403(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DEV_MODE on: impersonate LineMember → viewing HC's char (not theirs) → 403."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "1")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    impersonating_headers = {**CREATOR_HEADERS, "X-Impersonate-User": "LineMember"}

    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/timeline",
            headers=impersonating_headers,
        )
    assert resp.status_code == 403, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_impersonation_dev_off_ignored(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DEV_MODE off: X-Impersonate-User is IGNORED; real HC identity governs → 200."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    br_id = await _setup_br(tmp_path, monkeypatch)
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    # Even if header is present, prod ignores it.  Real user is HC → can see any char.
    impersonating_headers = {**CREATOR_HEADERS, "X-Impersonate-User": "LineMember"}

    with TestClient(create_app()) as client:
        resp = client.get(
            f"/api/brs/{br_id}/characters/{RAZOK_CHAR}/timeline",
            headers=impersonating_headers,
        )
    assert resp.status_code == 200, resp.text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_me_reflects_impersonated_user(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """/api/me with impersonation: returns LineMember identity + can_create_br=false."""
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "me_test.db"))

    from app.db.engine import init_models

    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()
    await init_models(get_settings())
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    impersonating_headers = {**CREATOR_HEADERS, "X-Impersonate-User": "LineMember"}

    with TestClient(create_app()) as client:
        resp = client.get("/api/me", headers=impersonating_headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user_name"] == "LineMember"
    assert data["can_create_br"] is False
    assert data["impersonation_available"] is True

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


@pytest.mark.asyncio
async def test_me_impersonation_available_false_in_prod(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """/api/me with DEV_MODE=0 → impersonation_available=false."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models, reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "me_prod.db"))

    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()
    await init_models(get_settings())
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        resp = client.get("/api/me", headers=MEMBER_HEADERS)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["impersonation_available"] is False

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


# ---------------------------------------------------------------------------
# GET /api/roster/users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roster_users_returns_demo_users(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """GET /api/roster/users returns the demo roster sorted by user_name."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models, reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "roster_test.db"))

    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()
    await init_models(get_settings())
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        resp = client.get("/api/roster/users", headers=MEMBER_HEADERS)

    assert resp.status_code == 200, resp.text
    users = resp.json()
    assert isinstance(users, list)
    assert len(users) >= 2
    user_names = [u["user_name"] for u in users]
    # Sorted by user_name
    assert user_names == sorted(user_names)
    # Spot check fields
    for u in users:
        assert "user_name" in u
        assert "main_character_id" in u
        assert "rank" in u

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()
