"""API tests for the AAR (After Action Report) endpoints.

Covers CRUD + the permission gates: FC/HC-only AAR write/delete, any-user
comments, author-only edit, author-or-moderator delete, reaction toggle +
validation, and cascade on AAR delete.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.db.models import BattleReport
from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

BR_ID = "aar-api"

# A second, unrelated non-elevated member (distinct from LineMember) to prove a
# member cannot edit/delete someone else's comment.
OTHER_MEMBER_HEADERS = {
    "Authorization": f"Bearer {TEST_TOKEN}",
    "X-User-Name": "OtherMember",
    "X-User-Rank": "Member",
    "X-User-Teams": "",
    "X-User-Main-Character-Id": "95000002",
}


async def _seed(session) -> None:  # type: ignore[no-untyped-def]
    session.add(BattleReport(
        br_id=BR_ID, source="demo", source_url="x", source_ref="r",
        created_by_user="t", status="ready", progress_pct=100,
        created_at=dt.datetime(2026, 7, 12, 2, 0),
        battle_at=dt.datetime(2026, 7, 12, 0, 0),
    ))
    await session.flush()


async def _make_app(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DEV_MODE", "0")
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    sm = get_sessionmaker(settings)
    async with sm() as session:
        await _seed(session)
        await session.commit()
    return create_app()


@pytest.mark.asyncio
async def test_aar_write_gate_and_read(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        # Empty to start; any user can read; viewer flags reflect role.
        g = client.get(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS)
        assert g.status_code == 200, g.text
        body = g.json()
        assert body["aar"] is None
        assert body["comments"] == []
        assert body["viewer"]["can_manage"] is False
        assert body["viewer"]["allowed_reactions"] == ["👍", "❤️", "🔥", "🎉", "😂", "o7"]

        # Non-elevated cannot write.
        assert client.put(
            f"/api/brs/{BR_ID}/aar", json={"body": "nope"}, headers=MEMBER_HEADERS
        ).status_code == 403

        # FC/HC can write, and it round-trips with authorship.
        w = client.put(
            f"/api/brs/{BR_ID}/aar",
            json={"body": "# Plan\n**Hold** the gate."},
            headers=CREATOR_HEADERS,
        )
        assert w.status_code == 200, w.text
        assert w.json()["created_by_user"] == "Ra'zok"

        g2 = client.get(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS).json()
        assert g2["aar"]["body"].startswith("# Plan")
        assert g2["viewer"]["can_manage"] is False

        # Empty body rejected.
        assert client.put(
            f"/api/brs/{BR_ID}/aar", json={"body": "   "}, headers=CREATOR_HEADERS
        ).status_code == 400


@pytest.mark.asyncio
async def test_comment_permissions(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        # Cannot comment before an AAR exists.
        assert client.post(
            f"/api/brs/{BR_ID}/aar/comments", json={"body": "hi"}, headers=MEMBER_HEADERS
        ).status_code == 400

        client.put(f"/api/brs/{BR_ID}/aar", json={"body": "AAR"}, headers=CREATOR_HEADERS)

        # Any authenticated user may comment.
        c = client.post(
            f"/api/brs/{BR_ID}/aar/comments", json={"body": "gf all"}, headers=MEMBER_HEADERS
        )
        assert c.status_code == 200, c.text
        cid = c.json()["comment_id"]
        assert c.json()["editable"] is True

        # A different member cannot edit someone else's comment.
        assert client.patch(
            f"/api/brs/{BR_ID}/aar/comments/{cid}",
            json={"body": "hacked"}, headers=OTHER_MEMBER_HEADERS,
        ).status_code == 403

        # The author can edit; updated_at is stamped.
        e = client.patch(
            f"/api/brs/{BR_ID}/aar/comments/{cid}",
            json={"body": "gf, gg"}, headers=MEMBER_HEADERS,
        )
        assert e.status_code == 200, e.text
        assert e.json()["updated_at"] is not None

        # A different member cannot delete it either.
        assert client.delete(
            f"/api/brs/{BR_ID}/aar/comments/{cid}", headers=OTHER_MEMBER_HEADERS
        ).status_code == 403

        # FC/HC may delete anyone's comment (moderation).
        assert client.delete(
            f"/api/brs/{BR_ID}/aar/comments/{cid}", headers=CREATOR_HEADERS
        ).status_code == 200

        assert client.get(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS).json()["comments"] == []


@pytest.mark.asyncio
async def test_reactions_toggle_and_validate(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.put(f"/api/brs/{BR_ID}/aar", json={"body": "AAR"}, headers=CREATOR_HEADERS)
        # React on a comment target (the reaction endpoint validates the target).
        c = client.post(
            f"/api/brs/{BR_ID}/aar/comments", json={"body": "c"}, headers=MEMBER_HEADERS
        ).json()
        cid = c["comment_id"]

        # Invalid emoji rejected.
        assert client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "💩"},
            headers=MEMBER_HEADERS,
        ).status_code == 400

        # Add a reaction on the comment.
        r1 = client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "o7"},
            headers=MEMBER_HEADERS,
        )
        assert r1.status_code == 200, r1.text
        grp = r1.json()["reactions"][0]
        assert grp["emoji"] == "o7" and grp["count"] == 1 and grp["reacted_by_me"] is True

        # Toggling again removes it (idempotent toggle).
        r2 = client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "o7"},
            headers=MEMBER_HEADERS,
        )
        assert r2.status_code == 200
        assert r2.json()["reactions"] == []

        # Another user's identical reaction counts separately; reacted_by_me is per-viewer.
        client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "🔥"},
            headers=MEMBER_HEADERS,
        )
        client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "🔥"},
            headers=OTHER_MEMBER_HEADERS,
        )
        seen = client.get(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS).json()
        cg = seen["comments"][0]["reactions"][0]
        assert cg["emoji"] == "🔥" and cg["count"] == 2 and cg["reacted_by_me"] is True


@pytest.mark.asyncio
async def test_delete_aar_cascades(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.put(f"/api/brs/{BR_ID}/aar", json={"body": "AAR"}, headers=CREATOR_HEADERS)
        cid = client.post(
            f"/api/brs/{BR_ID}/aar/comments", json={"body": "c"}, headers=MEMBER_HEADERS
        ).json()["comment_id"]
        client.post(
            f"/api/brs/{BR_ID}/aar/reactions",
            json={"target_type": "comment", "target_id": cid, "emoji": "🎉"},
            headers=MEMBER_HEADERS,
        )

        # Non-elevated cannot delete the AAR.
        assert client.delete(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS).status_code == 403

        # FC/HC delete clears body + comments + reactions.
        assert client.delete(f"/api/brs/{BR_ID}/aar", headers=CREATOR_HEADERS).status_code == 200
        after = client.get(f"/api/brs/{BR_ID}/aar", headers=CREATOR_HEADERS).json()
        assert after["aar"] is None
        assert after["comments"] == []

        # Reaction rows are gone too. SQLite reuses rowids after a full delete, so
        # the fresh comment may reclaim the old id — if the reaction rows had
        # survived they'd resurface here; that they don't proves the cascade.
        client.put(f"/api/brs/{BR_ID}/aar", json={"body": "AAR2"}, headers=CREATOR_HEADERS)
        client.post(
            f"/api/brs/{BR_ID}/aar/comments", json={"body": "c2"}, headers=MEMBER_HEADERS
        )
        fresh = client.get(f"/api/brs/{BR_ID}/aar", headers=MEMBER_HEADERS).json()
        assert fresh["comments"][0]["reactions"] == []


@pytest.mark.asyncio
async def test_unknown_br_404(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = await _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/brs/nope/aar", headers=CREATOR_HEADERS).status_code == 404
        assert client.put(
            "/api/brs/nope/aar", json={"body": "x"}, headers=CREATOR_HEADERS
        ).status_code == 404
