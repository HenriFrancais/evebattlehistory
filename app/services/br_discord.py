"""Fire-and-forget Discord fan-out when a Battle Report is created.

Pressing "Create BR" opens a thread in the FC/HC forum channel and posts an
announcement (linking back to the in-app BR) to the member channel. Both run in
a detached background task: a Discord outage or misconfigured bot never blocks
BR creation — it just means a missing thread link. Runs exactly once per BR
(from the create handler), so no idempotency bookkeeping is needed.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update

from app.config import Settings, get_app_config
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport
from app.observability.logging import log
from app.services.discord import DiscordMessage, get_discord_sender

_active_tasks: set[asyncio.Task[None]] = set()


def schedule_br_announce(settings: Settings, br_id: str, title: str | None) -> None:
    """Schedule the Discord fan-out for *br_id* as a detached background task."""
    task = asyncio.create_task(_run(settings, br_id, title), name=f"br-discord-{br_id}")
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def _run(settings: Settings, br_id: str, title: str | None) -> None:
    try:
        sender = get_discord_sender(settings)
        if not sender.configured:
            log.debug("br_discord.skipped", reason="not_configured", br_id=br_id)
            return

        cfg = get_app_config()
        name = title or f"Battle Report {br_id[:8]}"
        br_url = f"{cfg.public_base_url}/brs/{br_id}" if cfg.public_base_url else None

        # 1) FC/HC forum thread. Persist the jump link (gated to FC/HC on read).
        thread_body = f"{name}\n{br_url}" if br_url else name
        try:
            thread = await sender.create_forum_thread(
                cfg.discord_forum_channel_id, name, thread_body
            )
            if thread and thread.get("id") and thread.get("guild_id"):
                thread_url = (
                    f"https://discord.com/channels/{thread['guild_id']}/{thread['id']}"
                )
                session_maker = get_sessionmaker(settings)
                async with session_maker() as session:
                    await session.execute(
                        update(BattleReport)
                        .where(BattleReport.br_id == br_id)
                        .values(
                            discord_thread_id=int(thread["id"]),
                            discord_thread_url=thread_url,
                        )
                    )
                    await session.commit()
                log.info("br_discord.thread_created", br_id=br_id, thread_id=thread["id"])
        except Exception as exc:
            log.warning("br_discord.thread_failed", br_id=br_id, error=str(exc))

        # 2) Member-channel announcement linking back to the in-app BR.
        try:
            content = f"New battle report: {name}"
            if br_url:
                content = f"{content}\n{br_url}"
            await sender.send_to_channel(
                cfg.discord_member_channel_id, DiscordMessage(content=content)
            )
        except Exception as exc:
            log.warning("br_discord.announce_failed", br_id=br_id, error=str(exc))
    except Exception as exc:  # belt-and-braces: a background task must never crash loudly
        log.warning("br_discord.failed", br_id=br_id, error=str(exc))
