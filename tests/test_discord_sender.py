"""DiscordSender — send/thread/retry/rate-limit/no-op behaviour.

httpx.MockTransport lets us assert request shape and drive controlled responses
without hitting the real Discord API.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.services.discord import DiscordMessage, DiscordSender


def _settings(token: str = "tok-123") -> Settings:
    return Settings(nv_token="ignored", discord_bot_token=token)


def _make_sender(handler: Callable[[httpx.Request], httpx.Response]) -> DiscordSender:
    settings = _settings()
    sender = DiscordSender(settings)
    transport = httpx.MockTransport(handler)
    # Inject the mock transport on a pre-built client so the sender bypasses the
    # network. Matches DiscordSender._get_client's lazy-init contract.
    sender._client = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=transport,
        headers={"Authorization": f"Bot {settings.discord_bot_token}"},
    )
    return sender


# --- send_to_channel --------------------------------------------------------- #


async def test_send_no_op_when_token_empty():
    sender = DiscordSender(_settings(token=""))
    assert await sender.send_to_channel("12345", DiscordMessage(content="hi")) is False


async def test_send_no_op_when_channel_empty():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={})

    sender = _make_sender(handler)
    assert await sender.send_to_channel("", DiscordMessage(content="hi")) is False
    assert calls == []
    await sender.close()


async def test_send_posts_to_channel_messages_endpoint():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": "1"})

    sender = _make_sender(handler)
    ok = await sender.send_to_channel(
        "channel-42", DiscordMessage(content="hello", embeds=[{"title": "t"}])
    )
    assert ok is True
    assert len(seen) == 1
    assert seen[0].url.path == "/api/v10/channels/channel-42/messages"
    assert seen[0].headers["authorization"] == "Bot tok-123"
    body = seen[0].read()
    assert b"hello" in body
    assert b'"title":"t"' in body or b'"title": "t"' in body
    await sender.close()


async def test_429_honours_retry_after_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0.01"})
        return httpx.Response(200, json={})

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("app.services.discord.asyncio.sleep", fake_sleep)
    sender = _make_sender(handler)
    assert await sender.send_to_channel("c", DiscordMessage(content="x")) is True
    assert calls["n"] == 2
    assert sleeps and sleeps[0] == pytest.approx(0.01)
    await sender.close()


async def test_5xx_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    monkeypatch.setattr("app.services.discord.asyncio.sleep", lambda s: _noop())
    sender = _make_sender(handler)
    assert await sender.send_to_channel("c", DiscordMessage(content="x")) is False
    assert calls["n"] == 3  # _MAX_RETRIES
    await sender.close()


async def test_4xx_other_than_429_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, json={"message": "Missing Permissions"})

    monkeypatch.setattr("app.services.discord.asyncio.sleep", lambda s: _noop())
    sender = _make_sender(handler)
    assert await sender.send_to_channel("c", DiscordMessage(content="x")) is False
    assert calls["n"] == 1  # config errors aren't worth retrying
    await sender.close()


async def test_retry_after_from_json_body(monkeypatch):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"retry_after": 0.05})
        return httpx.Response(200, json={})

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("app.services.discord.asyncio.sleep", fake_sleep)
    sender = _make_sender(handler)
    assert await sender.send_to_channel("c", DiscordMessage(content="x")) is True
    assert sleeps[0] == pytest.approx(0.05)
    await sender.close()


# --- create_forum_thread ----------------------------------------------------- #


async def test_create_thread_no_op_when_token_empty():
    sender = DiscordSender(_settings(token=""))
    assert await sender.create_forum_thread("forum1", "Name", "body") is None


async def test_create_thread_no_op_when_channel_empty():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(201, json={"id": "1", "guild_id": "2"})

    sender = _make_sender(handler)
    assert await sender.create_forum_thread("", "Name", "body") is None
    assert calls == []
    await sender.close()


async def test_create_thread_posts_to_threads_endpoint_and_returns_object():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"id": "999", "guild_id": "111"})

    sender = _make_sender(handler)
    thread = await sender.create_forum_thread("forum-7", "Fight X", "see: http://app/brs/1")
    assert thread == {"id": "999", "guild_id": "111"}
    assert len(seen) == 1
    assert seen[0].url.path == "/api/v10/channels/forum-7/threads"
    body = seen[0].read()
    assert b"Fight X" in body
    assert b"http://app/brs/1" in body
    await sender.close()


async def test_create_thread_includes_allowed_mentions_when_role_given():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"id": "1", "guild_id": "2"})

    sender = _make_sender(handler)
    await sender.create_forum_thread(
        "forum-7", "Fight X", "<@&9001> Fight X", mention_role_ids=["9001"]
    )
    import json

    payload = json.loads(seen[0].read())
    assert payload["message"]["content"] == "<@&9001> Fight X"
    assert payload["message"]["allowed_mentions"] == {"roles": ["9001"]}
    await sender.close()


async def test_create_thread_omits_allowed_mentions_without_role():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"id": "1", "guild_id": "2"})

    sender = _make_sender(handler)
    await sender.create_forum_thread("forum-7", "Fight X", "Fight X")
    import json

    payload = json.loads(seen[0].read())
    assert "allowed_mentions" not in payload["message"]
    await sender.close()


async def test_create_thread_truncates_name_to_100():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"id": "1", "guild_id": "2"})

    sender = _make_sender(handler)
    long_name = "A" * 250
    await sender.create_forum_thread("forum-7", long_name, "body")
    import json

    payload = json.loads(seen[0].read())
    assert len(payload["name"]) == 100
    await sender.close()


async def test_create_thread_returns_none_on_permission_error(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Missing Permissions"})

    monkeypatch.setattr("app.services.discord.asyncio.sleep", lambda s: _noop())
    sender = _make_sender(handler)
    assert await sender.create_forum_thread("forum-7", "Name", "body") is None
    await sender.close()


async def _noop() -> None:
    return None
