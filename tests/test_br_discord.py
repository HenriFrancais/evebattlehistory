"""Discord fan-out on BR creation: the _run job, the get_br FC/HC gate, and the
create_br wiring.

The DiscordSender transport itself is covered in test_discord_sender.py; here we
use a fake sender so the job/endpoint logic is tested without any network.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN


class _FakeSender:
    def __init__(self, *, configured: bool = True, thread: dict | None = None) -> None:
        self._configured = configured
        self._thread = thread
        self.thread_call: tuple | None = None
        self.messages: list[tuple] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def create_forum_thread(self, forum_channel_id, name, content):
        self.thread_call = (forum_channel_id, name, content)
        return self._thread

    async def send_to_channel(self, channel_id, msg):
        self.messages.append((channel_id, msg))
        return True


async def _setup_db_with_br(tmp_path, monkeypatch, br_id: str):
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport

    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DISCORD_FORUM_CHANNEL_ID", "forum1")
    monkeypatch.setenv("DISCORD_MEMBER_CHANNEL_ID", "mem1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tools.example/ns/br")
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()

    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()
    return settings, session_maker


# --- _run job ---------------------------------------------------------------- #


async def test_run_stores_thread_url_and_announces(tmp_path, monkeypatch):
    from app.db.engine import reset_engine_for_tests
    from app.db.models import BattleReport
    from app.services import br_discord

    br_id = str(uuid.uuid4())
    settings, session_maker = await _setup_db_with_br(tmp_path, monkeypatch, br_id)

    fake = _FakeSender(thread={"id": "999", "guild_id": "111"})
    monkeypatch.setattr(br_discord, "get_discord_sender", lambda s: fake)

    await br_discord._run(settings, br_id, "Big Fight")

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
    assert br.discord_thread_id == 999
    assert br.discord_thread_url == "https://discord.com/channels/111/999"

    # Forum thread targeted the FC/HC channel, named from the title, body links the app.
    assert fake.thread_call[0] == "forum1"
    assert fake.thread_call[1] == "Big Fight"
    assert f"https://tools.example/ns/br/brs/{br_id}" in fake.thread_call[2]

    # Member announcement links back to the in-app BR.
    assert fake.messages and fake.messages[0][0] == "mem1"
    assert f"/brs/{br_id}" in fake.messages[0][1].content

    reset_engine_for_tests()


async def test_run_noop_when_not_configured(tmp_path, monkeypatch):
    from app.db.engine import reset_engine_for_tests
    from app.db.models import BattleReport
    from app.services import br_discord

    br_id = str(uuid.uuid4())
    settings, session_maker = await _setup_db_with_br(tmp_path, monkeypatch, br_id)

    fake = _FakeSender(configured=False)
    monkeypatch.setattr(br_discord, "get_discord_sender", lambda s: fake)

    await br_discord._run(settings, br_id, "Big Fight")

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
    assert br.discord_thread_id is None
    assert br.discord_thread_url is None
    assert fake.thread_call is None
    assert fake.messages == []

    reset_engine_for_tests()


async def test_run_announces_even_when_thread_fails(tmp_path, monkeypatch):
    """A None thread (create failed) must not block the member announcement."""
    from app.db.engine import reset_engine_for_tests
    from app.db.models import BattleReport
    from app.services import br_discord

    br_id = str(uuid.uuid4())
    settings, session_maker = await _setup_db_with_br(tmp_path, monkeypatch, br_id)

    fake = _FakeSender(thread=None)  # create_forum_thread returns None
    monkeypatch.setattr(br_discord, "get_discord_sender", lambda s: fake)

    await br_discord._run(settings, br_id, "Big Fight")

    async with session_maker() as session:
        br = (
            await session.execute(select(BattleReport).where(BattleReport.br_id == br_id))
        ).scalar_one()
    assert br.discord_thread_url is None  # nothing to store
    assert fake.messages and fake.messages[0][0] == "mem1"  # announcement still went out

    reset_engine_for_tests()


# --- get_br FC/HC gate ------------------------------------------------------- #


async def test_get_br_discord_link_gated(tmp_path, monkeypatch):
    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.db.models import BattleReport
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "g.db"))
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()

    settings = get_settings()
    await init_models(settings)
    br_id = str(uuid.uuid4())
    async with get_sessionmaker(settings)() as session:
        session.add(
            BattleReport(
                br_id=br_id,
                source="demo",
                source_url="http://x",
                source_ref="",
                created_by_user="test",
                status="ready",
                progress_pct=100,
                created_at=dt.datetime.now(dt.UTC),
                discord_thread_id=2,
                discord_thread_url="https://discord.com/channels/1/2",
            )
        )
        await session.commit()

    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        elevated = client.get(f"/api/brs/{br_id}", headers=CREATOR_HEADERS)
        member = client.get(f"/api/brs/{br_id}", headers=MEMBER_HEADERS)

    assert elevated.status_code == 200, elevated.text
    assert elevated.json()["discord_thread_url"] == "https://discord.com/channels/1/2"
    assert member.status_code == 200, member.text
    assert member.json()["discord_thread_url"] is None

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()


# --- create_br wiring -------------------------------------------------------- #


async def test_create_br_schedules_discord_announce(tmp_path, monkeypatch):
    from app.config import get_app_config, get_settings
    from app.db.engine import init_models, reset_engine_for_tests
    from app.main import create_app
    from app.roster.snapshot import reset_roster_store_for_tests

    monkeypatch.setenv("DEV_MODE", "0")
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "c.db"))
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_engine_for_tests()
    reset_roster_store_for_tests()

    await init_models(get_settings())
    get_app_config.cache_clear()
    reset_roster_store_for_tests()

    with TestClient(create_app()) as client:
        with (
            patch("app.api.brs.schedule_ingest"),
            patch("app.api.brs.schedule_br_announce") as announce,
        ):
            resp = client.post(
                "/api/brs",
                json={"url": "https://zkillboard.com/related/30000142/202606100000/", "title": "T"},
                headers=CREATOR_HEADERS,
            )

    assert resp.status_code == 202, resp.text
    br_id = resp.json()["br_id"]
    announce.assert_called_once()
    args = announce.call_args.args
    assert args[1] == br_id
    assert args[2] == "T"

    reset_engine_for_tests()
    get_settings.cache_clear()
    get_app_config.cache_clear()
    reset_roster_store_for_tests()
