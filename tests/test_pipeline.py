"""Tests for the ingest pipeline, background jobs, and BR API routes.

Demo setup:
  - URL used in tests: https://zkillboard.com/related/30002222/202606101500/
    The trailing slash makes url.split("/")[-1] == "" → resolves to "demo" fixture.
  - Alliance 99000001 = NV (friendly); Alliance 99000002 = hostiles
  - 5 demo killmails, 1 fight, result=loss (NV lost more ISK)
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

# A zkillboard URL that routes to "demo" fixture (trailing slash → last segment = "" → "demo")
_DEMO_URL = "https://zkillboard.com/related/30002222/202606101500/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_pending_br(session_maker, br_id: str | None = None) -> str:
    """Insert a pending BattleReport row; return br_id."""
    from app.db.models import BattleReport

    if br_id is None:
        br_id = str(uuid.uuid4())
    async with session_maker() as session:
        br = BattleReport(
            br_id=br_id,
            source="",
            source_url=_DEMO_URL,
            source_ref="",
            title=None,
            created_by_user="test",
            created_by_char_id=None,
            status="pending",
            progress_pct=0,
            created_at=dt.datetime.now(dt.UTC),
        )
        session.add(br)
        await session.commit()
    return br_id


# ---------------------------------------------------------------------------
# 1. run_ingest end-to-end (demo mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_ingest_happy_path(tmp_path, monkeypatch):
    """Full demo ingest: pending → ready, km_count=5, result not None, fights exist."""
    from sqlalchemy import func

    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport, BrFight
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

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
        fight_count = (
            await session.execute(
                select(func.count()).select_from(BrFight).where(BrFight.br_id == br_id)
            )
        ).scalar()

    assert br.status == "ready"
    assert br.km_count == 5
    assert br.result is not None
    assert fight_count == 1

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 2. run_ingest idempotent (run twice)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_ingest_idempotent(tmp_path, monkeypatch):
    """Running run_ingest twice on the same BR produces the same results."""
    from sqlalchemy import func

    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BrKillmail
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

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)
        # Reset status to pending to allow a second run
        async with session_maker() as session:
            from app.db.models import BattleReport

            br = (
                await session.execute(
                    select(BattleReport).where(BattleReport.br_id == br_id)
                )
            ).scalar_one()
            br.status = "pending"
            br.progress_pct = 0
            await session.commit()

        await run_ingest(settings, br_id)

    async with session_maker() as session:
        brkm_count = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        from app.db.models import BattleReport

        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert brkm_count == 5  # No duplicates
    assert br.status == "ready"

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 3. run_ingest error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_ingest_error_path(tmp_path, monkeypatch):
    """When resolve raises, status=error, error_text set, no exception propagates."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport
    from app.ingest.pipeline import run_ingest
    from app.ingest.sources.base import BrUnavailable

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

    class _FailSource:
        async def resolve(self, url: str) -> None:
            raise BrUnavailable("fixture not found")

    with patch("app.ingest.pipeline.get_source", return_value=_FailSource()):
        # Must NOT raise
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert br.status == "error"
    assert br.error_text is not None
    assert "fixture not found" in br.error_text

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 4. POST /api/brs: permissions + URL validation
# ---------------------------------------------------------------------------


def test_post_br_creator_gets_202(make_client, tmp_path, monkeypatch):
    """A High-Command user gets 202 and a pending br_id."""
    db_file = tmp_path / "test.db"

    with patch("app.api.brs.schedule_ingest"):
        client = make_client(DB_PATH=str(db_file))
        resp = client.post(
            "/api/brs",
            json={"url": _DEMO_URL},
            headers=CREATOR_HEADERS,
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending"
    assert "br_id" in data


def test_post_br_member_gets_403(make_client, tmp_path):
    """A Member user gets 403 Forbidden."""
    db_file = tmp_path / "test.db"

    with patch("app.api.brs.schedule_ingest"):
        client = make_client(DB_PATH=str(db_file))
        resp = client.post(
            "/api/brs",
            json={"url": _DEMO_URL},
            headers=MEMBER_HEADERS,
        )

    assert resp.status_code == 403


def test_post_br_bad_host_gets_400(make_client, tmp_path):
    """A URL with unsupported host gets 400."""
    db_file = tmp_path / "test.db"

    with patch("app.api.brs.schedule_ingest"):
        client = make_client(DB_PATH=str(db_file))
        resp = client.post(
            "/api/brs",
            json={"url": "https://example.com/some/br"},
            headers=CREATOR_HEADERS,
        )

    assert resp.status_code == 400


def test_post_br_www_prefix_accepted(make_client, tmp_path):
    """A URL with www.zkillboard.com should be accepted (www. stripped for check)."""
    db_file = tmp_path / "test.db"

    with patch("app.api.brs.schedule_ingest"):
        client = make_client(DB_PATH=str(db_file))
        resp = client.post(
            "/api/brs",
            json={"url": "https://www.zkillboard.com/related/30002222/202606101500/"},
            headers=CREATOR_HEADERS,
        )

    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# 5. GET /api/brs list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_brs_list_after_ingest(tmp_path, monkeypatch):
    """GET /api/brs returns the completed BR with result and correct win_rate."""
    from fastapi.testclient import TestClient

    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

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

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    # Boot a test client against the same DB
    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/brs", headers={"Authorization": f"Bearer {TEST_TOKEN}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total"] == 1
    # Demo result is "loss"
    assert data["summary"]["losses"] == 1
    assert data["summary"]["win_rate"] == 0.0
    assert len(data["brs"]) == 1
    assert data["brs"][0]["br_id"] == br_id

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 6. GET /api/brs/{id}, /status, /fights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_br_detail_and_status(tmp_path, monkeypatch):
    """GET /api/brs/{id} returns fights with sides; /status reflects ready; 404 for unknown."""
    from fastapi.testclient import TestClient

    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

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

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        # Detail endpoint
        resp = client.get(f"/api/brs/{br_id}", headers=hdrs)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["br_id"] == br_id
        assert detail["status"] == "ready"
        assert len(detail["fights"]) == 1
        fight = detail["fights"][0]
        assert fight["system_id"] > 0
        assert len(fight["sides"]) >= 2

        # Status endpoint
        status_resp = client.get(f"/api/brs/{br_id}/status", headers=hdrs)
        assert status_resp.status_code == 200
        st = status_resp.json()
        assert st["status"] == "ready"
        assert st["progress_pct"] == 100

        # Fights endpoint
        fights_resp = client.get(f"/api/brs/{br_id}/fights", headers=hdrs)
        assert fights_resp.status_code == 200
        fights = fights_resp.json()
        assert len(fights) == 1

        # 404 for unknown br_id
        resp404 = client.get("/api/brs/no-such-br", headers=hdrs)
        assert resp404.status_code == 404

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 7. sweep_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_pending(tmp_path, monkeypatch):
    """sweep_pending finds non-terminal BRs and schedules them; returns correct count."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.ingest.jobs import sweep_pending

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    # Create two BRs: one pending, one clustering (both non-terminal), one ready (terminal)
    from app.db.models import BattleReport

    async with session_maker() as session:
        for status in ("pending", "clustering", "ready"):
            br = BattleReport(
                br_id=str(uuid.uuid4()),
                source="",
                source_url=_DEMO_URL,
                source_ref="",
                title=None,
                created_by_user="test",
                status=status,
                progress_pct=0,
                created_at=dt.datetime.now(dt.UTC),
            )
            session.add(br)
        await session.commit()

    scheduled: list[str] = []

    with patch(
        "app.ingest.jobs.schedule_ingest",
        side_effect=lambda _s, br_id: scheduled.append(br_id),
    ):
        count = await sweep_pending(settings)

    assert count == 2
    assert len(scheduled) == 2

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 8. GET /api/brs/{br_id}/status for unknown ID returns 404
# ---------------------------------------------------------------------------


def test_get_status_unknown_br(make_client, tmp_path):
    """GET /api/brs/{unknown}/status returns 404."""
    db_file = tmp_path / "test.db"
    client = make_client(DB_PATH=str(db_file))
    resp = client.get(
        "/api/brs/no-such-br/status",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9. GET /api/brs ordering: battle_at desc, nulls last
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_brs_list_ordered_by_battle_at(tmp_path, monkeypatch):
    """GET /api/brs returns BRs newest battle_at first, regardless of insert order.

    We insert BR-A (created first, older battle_at) then BR-B (created second,
    newer battle_at). The list must return BR-B first.
    A third BR with no battle_at must appear last (nulls last).
    """
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)

    # Shared timestamps
    t_old = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    t_mid = dt.datetime(2026, 3, 15, 12, 0, 0, tzinfo=dt.UTC)
    t_new = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.UTC)

    # Insert order: old-battle BR first, then new-battle BR, then null-battle BR
    id_old = str(uuid.uuid4())
    id_new = str(uuid.uuid4())
    id_null = str(uuid.uuid4())

    async with session_maker() as session:
        # BR-A: submitted first (created_at=t_old) but battle happened on t_old
        br_a = BattleReport(
            br_id=id_old,
            source="demo",
            source_url=_DEMO_URL,
            source_ref="ref-a",
            title="Old Battle",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=t_old,
            battle_at=t_old,
        )
        # BR-B: submitted second (created_at=t_mid) but battle happened later (t_new)
        br_b = BattleReport(
            br_id=id_new,
            source="demo",
            source_url=_DEMO_URL,
            source_ref="ref-b",
            title="New Battle",
            created_by_user="test",
            status="ready",
            progress_pct=100,
            created_at=t_mid,
            battle_at=t_new,
        )
        # BR-C: battle_at is null — must appear last
        br_c = BattleReport(
            br_id=id_null,
            source="demo",
            source_url=_DEMO_URL,
            source_ref="ref-c",
            title="Pending Battle",
            created_by_user="test",
            status="pending",
            progress_pct=0,
            created_at=t_mid,
            battle_at=None,
        )
        session.add_all([br_a, br_b, br_c])
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        resp = client.get("/api/brs", headers=hdrs)

    assert resp.status_code == 200
    data = resp.json()
    returned_ids = [b["br_id"] for b in data["brs"]]

    assert returned_ids[0] == id_new, "Newest battle_at should be first"
    assert returned_ids[1] == id_old, "Older battle_at should be second"
    assert returned_ids[2] == id_null, "Null battle_at should be last"

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 10. source_url is present in GET /api/brs and GET /api/brs/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_url_in_list_and_detail(tmp_path, monkeypatch):
    """GET /api/brs and GET /api/brs/{id} both include source_url from the BR row."""
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)

    session_maker = get_sessionmaker(settings)
    br_id = await _create_pending_br(session_maker)  # source_url = _DEMO_URL

    get_app_config.cache_clear()
    app = create_app()
    hdrs = {"Authorization": f"Bearer {TEST_TOKEN}"}
    with TestClient(app) as client:
        list_resp = client.get("/api/brs", headers=hdrs)
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert len(list_data["brs"]) == 1
        assert list_data["brs"][0]["source_url"] == _DEMO_URL

        detail_resp = client.get(f"/api/brs/{br_id}", headers=hdrs)
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["source_url"] == _DEMO_URL

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
