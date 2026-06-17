"""Background job scheduling for BR ingest."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import Settings
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport
from app.ingest.pipeline import run_ingest
from app.observability.logging import log

_active_tasks: set[asyncio.Task[None]] = set()

_NON_TERMINAL = ("pending", "resolving", "enriching", "persisting", "clustering")


def schedule_ingest(settings: Settings, br_id: str) -> None:
    """Schedule a background ingest task for the given BR ID."""
    task = asyncio.create_task(run_ingest(settings, br_id), name=f"ingest-{br_id}")
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def sweep_pending(settings: Settings) -> int:
    """Find all BRs in non-terminal states and schedule ingest for each.

    Returns the number of BRs rescheduled.
    """
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        result = await session.execute(
            select(BattleReport.br_id).where(BattleReport.status.in_(_NON_TERMINAL))
        )
        br_ids = list(result.scalars())

    for br_id in br_ids:
        schedule_ingest(settings, br_id)

    log.info("jobs.sweep_pending", count=len(br_ids))
    return len(br_ids)
