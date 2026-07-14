"""Stamp ``Broadcast.fight_id`` by time-window overlap with a BR's fights.

A strict subset of ``app/logs/associate.py``'s logic: broadcasts already carry the
authoritative ``br_id`` (manual association), so this only distributes them across the
BR's individual fights using the same ±120s padded-window overlap rule.  Broadcasts
that fall in a fight's ±pad gap keep ``br_id`` but stay ``fight_id=None``.

No bucket rebuild or tackle dedupe is needed: broadcasts are a single fleet-wide file,
not the multi-observer combat logs those passes exist for.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BrFight, Broadcast, Fight

#: Match the association pad used for gamelogs (app/logs/associate.py).
PAD_SECONDS: int = 120


async def associate_broadcasts_for_br(
    session: AsyncSession, br_id: str, pad_seconds: int = PAD_SECONDS
) -> int:
    """Reset and re-stamp ``fight_id`` on every broadcast for *br_id*. Returns count stamped."""
    # Idempotent reset. synchronize_session=False mirrors associate.py: avoids the
    # in-Python identity-map comparison that TypeErrors on tz-aware vs naive ts.
    await session.execute(
        update(Broadcast).where(Broadcast.br_id == br_id).values(fight_id=None),
        execution_options={"synchronize_session": False},
    )

    fights = list(
        (
            await session.execute(
                select(Fight)
                .join(BrFight, BrFight.fight_id == Fight.fight_id)
                .where(BrFight.br_id == br_id)
            )
        ).scalars()
    )

    pad = dt.timedelta(seconds=pad_seconds)
    stamped = 0
    for fight in fights:
        window_start = fight.started_at - pad
        window_end = fight.ended_at + pad
        result = await session.execute(
            update(Broadcast)
            .where(Broadcast.br_id == br_id)
            .where(Broadcast.fight_id.is_(None))  # first fight in iteration order wins
            .where(Broadcast.ts >= window_start)
            .where(Broadcast.ts <= window_end)
            .values(fight_id=fight.fight_id),
            execution_options={"synchronize_session": False},
        )
        stamped += int(getattr(result, "rowcount", 0) or 0)
    await session.flush()
    return stamped
