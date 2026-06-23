"""E4a tests: BR multi-source model, merge/refresh, edit endpoints.

TDD — written before production code. All tests should FAIL initially.

Demo setup:
- Link source → resolved_br_demo.json → refs [101..105] (5 kills)
- Window source → resolved_window_demo.json → refs [106, 107] (2 kills)
- Together: union = [101..107] = 7 kills (no overlap)
- For per-source error isolation: one bad source (unsupported URL) + one good source
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

_DEMO_LINK_URL = "https://zkillboard.com/related/30002222/202606101500/"
_DEMO_BAD_URL = "https://unsupported-source.example.com/br/999"

# A window source pointing at a real system; in demo mode this resolves offline
# via resolved_window_demo.json
_DEMO_WINDOW = {
    "kind": "window",
    "system_id": 31002222,
    "window_start": "2026-06-10T18:00:00Z",
    "window_end": "2026-06-10T22:00:00Z",
    "label": "J-Space Window",
}

_DEMO_LINK_SOURCE = {
    "kind": "link",
    "url": _DEMO_LINK_URL,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup_env(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    return str(db_file)


def _teardown() -> None:
    from app.config import get_app_config, get_settings
    from app.db.engine import reset_engine_for_tests

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()


# ---------------------------------------------------------------------------
# 1. BrSource table: create with {sources:[link, window]} → 2 BrSource rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_br_with_two_sources_creates_br_source_rows(tmp_path, monkeypatch):
    """POST /api/brs with sources:[link, window] creates 2 BrSource rows."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrSource
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "title": "Multi-source BR",
                    "sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW],
                },
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202, resp.text
    data = resp.json()
    br_id = data["br_id"]
    assert data["status"] == "pending"

    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        rows = list(
            (
                await session.execute(
                    select(BrSource).where(BrSource.br_id == br_id)
                )
            ).scalars()
        )

    assert len(rows) == 2
    kinds = {r.kind for r in rows}
    assert kinds == {"link", "window"}

    _teardown()


# ---------------------------------------------------------------------------
# 2. Back-compat: {url} → one link BrSource, ingest works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_br_backcompat_url_creates_one_link_source(tmp_path, monkeypatch):
    """POST /api/brs with {url} (old path) creates one link BrSource and ingests."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrKillmail, BrSource
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)


    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"url": _DEMO_LINK_URL},
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202, resp.text
    br_id = resp.json()["br_id"]

    # Run the ingest manually
    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        sources = list(
            (
                await session.execute(
                    select(BrSource).where(BrSource.br_id == br_id)
                )
            ).scalars()
        )
        km_count = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        from app.db.models import BattleReport

        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert len(sources) == 1
    assert sources[0].kind == "link"
    assert sources[0].url == _DEMO_LINK_URL
    assert km_count == 5
    assert br.status == "ready"

    _teardown()


# ---------------------------------------------------------------------------
# 3. Multi-source merge: link (5 KMs) + window (2 KMs) → 7 BrKillmail rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_source_merge_unions_killmails(tmp_path, monkeypatch):
    """Ingest with link + window sources merges KMs: union count = 5+2 = 7."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrKillmail, BrSource
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "title": "Multi-source BR",
                    "sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW],
                },
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202
    br_id = resp.json()["br_id"]

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        km_count = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        sources = list(
            (
                await session.execute(
                    select(BrSource).where(BrSource.br_id == br_id)
                )
            ).scalars()
        )
        from app.db.models import BattleReport

        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    # Both sources should be ok
    src_statuses = {s.kind: s.status for s in sources}
    assert src_statuses.get("link") == "ok"
    assert src_statuses.get("window") == "ok"
    # Total KM count = 5 (link) + 2 (window) = 7
    assert km_count == 7
    assert br.km_count == 7
    assert br.status == "ready"

    _teardown()


# ---------------------------------------------------------------------------
# 4. Refresh idempotency: running ingest twice doesn't duplicate killmails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_is_idempotent(tmp_path, monkeypatch):
    """Running run_ingest twice on a multi-source BR doesn't duplicate KMs."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BattleReport, BrKillmail
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW]},
                headers=CREATOR_HEADERS,
            )
    br_id = resp.json()["br_id"]

    cfg = AppConfig(our_alliance_ids=[99000001], our_corp_ids=[])
    with patch("app.ingest.pipeline.get_app_config", return_value=cfg):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
        br.status = "pending"
        br.progress_pct = 0
        await session.commit()

    with patch("app.ingest.pipeline.get_app_config", return_value=cfg):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        km_count = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert km_count == 7  # No duplication
    assert br.status == "ready"
    assert br.km_count == 7

    _teardown()


# ---------------------------------------------------------------------------
# 5. Per-source error isolation: bad URL → that source errors; good source merges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_source_error_isolation(tmp_path, monkeypatch):
    """One source with a bad URL → status=error on that BrSource; other source proceeds."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BattleReport, BrKillmail, BrSource
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "sources": [
                        _DEMO_LINK_SOURCE,  # good
                        {"kind": "link", "url": _DEMO_BAD_URL},  # unsupported host → error
                    ]
                },
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202, resp.text
    br_id = resp.json()["br_id"]

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        sources = list(
            (
                await session.execute(
                    select(BrSource).where(BrSource.br_id == br_id)
                )
            ).scalars()
        )
        km_count = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    src_by_url = {s.url: s for s in sources}
    good_src = src_by_url[_DEMO_LINK_URL]
    bad_src = src_by_url[_DEMO_BAD_URL]

    # Good source resolved ok
    assert good_src.status == "ok"
    assert good_src.km_count == 5

    # Bad source is in error; others not affected
    assert bad_src.status == "error"
    assert bad_src.error_text is not None

    # BR is ready (some sources ok) with the 5 good KMs
    assert br.status == "ready"
    assert km_count == 5

    _teardown()


# ---------------------------------------------------------------------------
# 6. PATCH /api/brs/{br_id} title
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_title(tmp_path, monkeypatch):
    """PATCH /api/brs/{br_id} updates title; 404 for unknown; 403 for member."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BattleReport
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"url": _DEMO_LINK_URL, "title": "Original Title"},
                headers=CREATOR_HEADERS,
            )
        br_id = resp.json()["br_id"]

        # Creator can update title
        patch_resp = client.patch(
            f"/api/brs/{br_id}",
            json={"title": "Updated Title"},
            headers=CREATOR_HEADERS,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["title"] == "Updated Title"

        # Member gets 403
        member_resp = client.patch(
            f"/api/brs/{br_id}",
            json={"title": "Hacked"},
            headers=MEMBER_HEADERS,
        )
        assert member_resp.status_code == 403

        # 404 for unknown br
        not_found_resp = client.patch(
            "/api/brs/no-such-br",
            json={"title": "Doesn't Matter"},
            headers=CREATOR_HEADERS,
        )
        assert not_found_resp.status_code == 404

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert br.title == "Updated Title"

    _teardown()


# ---------------------------------------------------------------------------
# 7. GET /api/brs/{br_id}/sources lists sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sources(tmp_path, monkeypatch):
    """GET /api/brs/{br_id}/sources returns all BrSource rows for the BR."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW],
                },
                headers=CREATOR_HEADERS,
            )
        br_id = resp.json()["br_id"]

        sources_resp = client.get(
            f"/api/brs/{br_id}/sources",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )

    assert sources_resp.status_code == 200, sources_resp.text
    sources = sources_resp.json()
    assert len(sources) == 2
    kinds = {s["kind"] for s in sources}
    assert kinds == {"link", "window"}

    _teardown()


# ---------------------------------------------------------------------------
# 8. POST /api/brs/{br_id}/sources adds a source and triggers refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_source_triggers_refresh(tmp_path, monkeypatch):
    """POST /api/brs/{br_id}/sources adds a source (status pending) and triggers ingest."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrKillmail, BrSource
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    cfg = AppConfig(our_alliance_ids=[99000001], our_corp_ids=[])

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"sources": [_DEMO_LINK_SOURCE]},
                headers=CREATOR_HEADERS,
            )
        br_id = resp.json()["br_id"]

    with patch("app.ingest.pipeline.get_app_config", return_value=cfg):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        km_before = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
    assert km_before == 5

    # Add a window source (should trigger refresh)
    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest") as mock_schedule:
            add_resp = client.post(
                f"/api/brs/{br_id}/sources",
                json=_DEMO_WINDOW,
                headers=CREATOR_HEADERS,
            )
        assert add_resp.status_code == 202, add_resp.text
        mock_schedule.assert_called_once()

    # Now actually run the ingest (simulating schedule_ingest)
    with patch("app.ingest.pipeline.get_app_config", return_value=cfg):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        km_after = (
            await session.execute(
                select(func.count()).select_from(BrKillmail).where(BrKillmail.br_id == br_id)
            )
        ).scalar()
        source_count = (
            await session.execute(
                select(func.count()).select_from(BrSource).where(BrSource.br_id == br_id)
            )
        ).scalar()

    assert km_after == 7  # 5 from link + 2 from window
    assert source_count == 2

    _teardown()


# ---------------------------------------------------------------------------
# 9. DELETE /api/brs/{br_id}/sources/{source_id} removes source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_source(tmp_path, monkeypatch):
    """DELETE /api/brs/{br_id}/sources/{source_id} removes the source; gated."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrSource
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW]},
                headers=CREATOR_HEADERS,
            )
        br_id = resp.json()["br_id"]

        # Get sources to find source_id
        sources_resp = client.get(
            f"/api/brs/{br_id}/sources",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        sources = sources_resp.json()
        window_src = next(s for s in sources if s["kind"] == "window")
        source_id = window_src["source_id"]

        # Member can't delete
        member_del = client.delete(
            f"/api/brs/{br_id}/sources/{source_id}",
            headers=MEMBER_HEADERS,
        )
        assert member_del.status_code == 403

        # Creator can delete; triggers refresh
        with patch("app.api.brs.schedule_ingest") as mock_schedule:
            del_resp = client.delete(
                f"/api/brs/{br_id}/sources/{source_id}",
                headers=CREATOR_HEADERS,
            )
        assert del_resp.status_code == 204, del_resp.text
        mock_schedule.assert_called_once()

    # Verify the source is gone
    async with session_maker() as session:
        remaining = list(
            (
                await session.execute(
                    select(BrSource).where(BrSource.br_id == br_id)
                )
            ).scalars()
        )
    assert len(remaining) == 1
    assert remaining[0].kind == "link"

    _teardown()


# ---------------------------------------------------------------------------
# 10. POST /api/brs/{br_id}/refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_refresh(tmp_path, monkeypatch):
    """POST /api/brs/{br_id}/refresh triggers ingest; gated; 404 for unknown."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={"url": _DEMO_LINK_URL},
                headers=CREATOR_HEADERS,
            )
        br_id = resp.json()["br_id"]

        # Member can't refresh
        member_resp = client.post(
            f"/api/brs/{br_id}/refresh",
            headers=MEMBER_HEADERS,
        )
        assert member_resp.status_code == 403

        # Creator can refresh
        with patch("app.api.brs.schedule_ingest") as mock_schedule:
            refresh_resp = client.post(
                f"/api/brs/{br_id}/refresh",
                headers=CREATOR_HEADERS,
            )
        assert refresh_resp.status_code == 202, refresh_resp.text
        assert "status" in refresh_resp.json()
        mock_schedule.assert_called_once()

        # 404 for unknown
        not_found = client.post(
            "/api/brs/no-such-br/refresh",
            headers=CREATOR_HEADERS,
        )
        assert not_found.status_code == 404

    _teardown()


# ---------------------------------------------------------------------------
# 11. Window validation: start >= end → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_validation_start_ge_end(tmp_path, monkeypatch):
    """POST /api/brs with window start>=end returns 400."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/brs",
            json={
                "sources": [
                    {
                        "kind": "window",
                        "system_id": 31002222,
                        "window_start": "2026-06-10T22:00:00Z",
                        "window_end": "2026-06-10T18:00:00Z",  # end before start
                    }
                ]
            },
            headers=CREATOR_HEADERS,
        )
    assert resp.status_code == 400, resp.text

    _teardown()


# ---------------------------------------------------------------------------
# 12. All mutation endpoints gated: MEMBER_HEADERS → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gating_on_all_mutations(tmp_path, monkeypatch):
    """All mutation endpoints return 403 for a plain Member."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            # POST /api/brs as member
            r = client.post(
                "/api/brs",
                json={"url": _DEMO_LINK_URL},
                headers=MEMBER_HEADERS,
            )
            assert r.status_code == 403

            # Create a valid BR as creator first
            r2 = client.post(
                "/api/brs",
                json={"url": _DEMO_LINK_URL},
                headers=CREATOR_HEADERS,
            )
            br_id = r2.json()["br_id"]

        # PATCH title as member
        r3 = client.patch(
            f"/api/brs/{br_id}",
            json={"title": "Hacked"},
            headers=MEMBER_HEADERS,
        )
        assert r3.status_code == 403

        # POST sources as member
        r4 = client.post(
            f"/api/brs/{br_id}/sources",
            json=_DEMO_WINDOW,
            headers=MEMBER_HEADERS,
        )
        assert r4.status_code == 403

        # POST refresh as member
        r5 = client.post(f"/api/brs/{br_id}/refresh", headers=MEMBER_HEADERS)
        assert r5.status_code == 403

        # Add a source as creator so we have a source_id to attempt deleting
        r6_create = client.post(
            "/api/brs",
            json={"sources": [_DEMO_LINK_SOURCE, _DEMO_WINDOW]},
            headers=CREATOR_HEADERS,
        )
        br_id2 = r6_create.json()["br_id"]
        sources_resp = client.get(
            f"/api/brs/{br_id2}/sources",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        source_id = sources_resp.json()[0]["source_id"]

        # DELETE source as member → 403
        r6 = client.delete(
            f"/api/brs/{br_id2}/sources/{source_id}",
            headers=MEMBER_HEADERS,
        )
        assert r6.status_code == 403

    _teardown()


# ---------------------------------------------------------------------------
# 13. All-sources-error sets BR.error_text (M5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_sources_error_sets_br_error_text(tmp_path, monkeypatch):
    """When all sources fail, run_ingest sets BR.status=error and BR.error_text."""
    from app.config import AppConfig, get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BattleReport
    from app.ingest.pipeline import run_ingest
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "sources": [
                        {"kind": "link", "url": _DEMO_BAD_URL},  # unsupported → error
                    ]
                },
                headers=CREATOR_HEADERS,
            )
    assert resp.status_code == 202, resp.text
    br_id = resp.json()["br_id"]

    with patch(
        "app.ingest.pipeline.get_app_config",
        return_value=AppConfig(our_alliance_ids=[99000001], our_corp_ids=[]),
    ):
        await run_ingest(settings, br_id)

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()

    assert br.status == "error"
    assert br.error_text is not None
    assert len(br.error_text) > 0

    _teardown()


# ---------------------------------------------------------------------------
# Window source via system NAME (resolves to system_id server-side)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_window_source_resolves_system_name(tmp_path, monkeypatch):
    """A window source given system_name resolves to system_id via solar_system."""
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.db.models import BrSource, SolarSystem
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)

    # Seed the local solar_system table so resolution needs no network.
    async with session_maker() as session:
        session.add(SolarSystem(system_id=31002502, name="J125122", security=-1.0))
        await session.commit()

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "title": "Name-based window",
                    "sources": [
                        {
                            "kind": "window",
                            "system_name": "j125122",  # case-insensitive
                            "window_start": "2025-02-19T19:00:00Z",
                            "window_end": "2025-02-19T22:00:00Z",
                        }
                    ],
                },
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202, resp.text
    br_id = resp.json()["br_id"]

    async with session_maker() as session:
        src = (
            await session.execute(select(BrSource).where(BrSource.br_id == br_id))
        ).scalar_one()
    assert src.kind == "window"
    assert src.system_id == 31002502

    _teardown()


@pytest.mark.asyncio
async def test_create_window_source_unknown_name_400(tmp_path, monkeypatch):
    """An unresolvable system_name yields HTTP 400 (demo ESI returns nothing)."""
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models
    from app.main import create_app

    _setup_env(tmp_path, monkeypatch)
    settings = get_settings()
    await init_models(settings)

    get_app_config.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        with patch("app.api.brs.schedule_ingest"):
            resp = client.post(
                "/api/brs",
                json={
                    "sources": [
                        {
                            "kind": "window",
                            "system_name": "NotARealSystem999",
                            "window_start": "2025-02-19T19:00:00Z",
                            "window_end": "2025-02-19T22:00:00Z",
                        }
                    ],
                },
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 400, resp.text
    assert "Unknown solar system" in resp.text

    _teardown()
