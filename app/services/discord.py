"""Reusable Discord bot sender.

Bot-token-based, channel-id-targeted. The bot token is shared across features
(and shared with the sibling `router` project); each caller passes its own
channel ID.

Empty ``DISCORD_BOT_TOKEN`` → all calls are no-ops (logged, never raised). Keeps
local dev and tests frictionless and makes "the bot is misconfigured" a quiet
failure that never blocks BR creation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.observability.logging import log

_DISCORD_API_BASE = "https://discord.com/api/v10"
_MAX_RETRIES = 3


@dataclass(slots=True)
class DiscordMessage:
    content: str = ""
    embeds: list[dict] = field(default_factory=list)


class DiscordSender:
    def __init__(self, settings: Settings) -> None:
        self._token = settings.discord_bot_token
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_DISCORD_API_BASE,
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={"Authorization": f"Bot {self._token}"},
            )
        return self._client

    async def _post_with_retries(
        self, url: str, payload: dict[str, object], *, ctx: str
    ) -> httpx.Response | None:
        """POST *payload* to *url*, retrying transient failures. Returns the
        successful response, or None on persistent failure. Never raises.

        429 → honour Retry-After; 5xx / network error → exponential backoff;
        other 4xx (permission/config) → log body and give up immediately.
        """
        client = self._get_client()
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.post(url, json=payload)
            except httpx.RequestError as exc:
                log.warning(
                    "discord.request_error", error=str(exc), attempt=attempt + 1, ctx=ctx
                )
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if resp.is_success:
                return resp

            if resp.status_code == 429:
                retry_after = self._extract_retry_after(resp)
                log.warning(
                    "discord.rate_limited",
                    retry_after_s=retry_after,
                    attempt=attempt + 1,
                    ctx=ctx,
                )
                await asyncio.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600:
                backoff = min(2**attempt, 8)
                log.warning(
                    "discord.server_error",
                    status=resp.status_code,
                    attempt=attempt + 1,
                    backoff_s=backoff,
                    ctx=ctx,
                )
                await asyncio.sleep(backoff)
                continue

            # 4xx other than 429 — config / permission errors. Retrying won't help.
            log.warning(
                "discord.client_error", status=resp.status_code, body=resp.text[:200], ctx=ctx
            )
            return None

        log.warning("discord.gave_up", ctx=ctx)
        return None

    async def send_to_channel(self, channel_id: str, msg: DiscordMessage) -> bool:
        """Post a message to a Discord channel. Returns True on success, False
        on persistent failure or when the sender isn't configured."""
        if not self._token:
            log.debug("discord.skipped", reason="no_token", channel_id=channel_id)
            return False
        if not channel_id:
            log.debug("discord.skipped", reason="no_channel")
            return False

        payload: dict[str, object] = {}
        if msg.content:
            payload["content"] = msg.content
        if msg.embeds:
            payload["embeds"] = msg.embeds

        resp = await self._post_with_retries(
            f"/channels/{channel_id}/messages", payload, ctx=f"message:{channel_id}"
        )
        return resp is not None

    async def create_forum_thread(
        self,
        forum_channel_id: str,
        name: str,
        content: str,
        *,
        mention_role_ids: list[str] | None = None,
    ) -> dict | None:
        """Create a forum-channel thread (Discord "Start Thread in Forum Channel").

        ``content`` should already contain any ``<@&id>`` role mentions; pass the
        same ids in ``mention_role_ids`` so ``allowed_mentions`` lets the ping fire
        (and nothing else — e.g. no accidental @everyone).

        Returns the created thread channel object (has ``id`` and ``guild_id``)
        on success, or None when unconfigured / on persistent failure.
        """
        if not self._token:
            log.debug("discord.skipped", reason="no_token", channel_id=forum_channel_id)
            return None
        if not forum_channel_id:
            log.debug("discord.skipped", reason="no_forum_channel")
            return None

        message: dict[str, object] = {"content": content}
        if mention_role_ids:
            message["allowed_mentions"] = {"roles": mention_role_ids}
        payload: dict[str, object] = {
            "name": name[:100],
            "message": message,
        }
        resp = await self._post_with_retries(
            f"/channels/{forum_channel_id}/threads", payload, ctx=f"thread:{forum_channel_id}"
        )
        if resp is None:
            return None
        try:
            body = resp.json()
        except Exception as exc:  # malformed 2xx body — treat as failure
            log.warning("discord.thread_bad_json", error=str(exc), channel_id=forum_channel_id)
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _extract_retry_after(resp: httpx.Response) -> float:
        header = resp.headers.get("retry-after")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        try:
            body = resp.json()
            if isinstance(body, dict):
                ra = body.get("retry_after")
                if isinstance(ra, int | float):
                    return max(0.0, float(ra))
        except Exception:
            pass
        return 1.0

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_discord_sender: DiscordSender | None = None


def get_discord_sender(settings: Settings) -> DiscordSender:
    global _discord_sender
    if _discord_sender is None:
        _discord_sender = DiscordSender(settings)
    return _discord_sender


def reset_discord_sender_for_tests() -> None:
    global _discord_sender
    _discord_sender = None
