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

# Ingest serialization ------------------------------------------------------- #
# SQLite allows only one writer. Firing an unguarded task per source-edit/refresh
# let two run_ingest coroutines for the same BR race on that writer and die with
# "database is locked" mid-ingest (also 500-ing any delete issued at the same
# moment). We therefore run at most ONE ingest at a time (global lock) and coalesce
# a burst of schedules for the same BR into a single trailing re-run.
_ingest_lock = asyncio.Lock()
_ingest_running: set[str] = set()  # br_ids with a live serialized task
_ingest_rerun: set[str] = set()  # br_ids scheduled again while their task ran

_NON_TERMINAL = ("pending", "resolving", "enriching", "persisting", "clustering")


def _reset_ingest_state_for_tests() -> None:
    """Clear module-level ingest bookkeeping between tests.

    Also mints a fresh ``_ingest_lock``: pytest-asyncio runs each test in a new
    event loop, and an ``asyncio.Lock`` reused across loops orphans its waiters.
    Production keeps a single loop for the process lifetime, so this never fires there.
    """
    global _ingest_lock
    _active_tasks.clear()
    _ingest_running.clear()
    _ingest_rerun.clear()
    _ingest_lock = asyncio.Lock()


async def _run_ingest_serialized(settings: Settings, br_id: str) -> None:
    """Run ingest for *br_id* under the global lock, then honour any coalesced re-run.

    All state mutations below happen between ``await`` points, so in the single
    asyncio loop they are atomic w.r.t. ``schedule_ingest`` — no lost or duplicated
    runs.
    """
    try:
        while True:
            async with _ingest_lock:
                await run_ingest(settings, br_id)
            if br_id in _ingest_rerun:
                _ingest_rerun.discard(br_id)
                continue  # a schedule arrived mid-run: re-ingest once with latest state
            break
    finally:
        _ingest_running.discard(br_id)
        _ingest_rerun.discard(br_id)


def schedule_ingest(settings: Settings, br_id: str) -> None:
    """Schedule a background ingest for *br_id*, serialized and coalesced.

    If an ingest for this BR is already in flight, flag a single trailing re-run
    instead of starting a second, concurrent task.
    """
    if br_id in _ingest_running:
        _ingest_rerun.add(br_id)
        return
    _ingest_running.add(br_id)
    task = asyncio.create_task(
        _run_ingest_serialized(settings, br_id), name=f"ingest-{br_id}"
    )
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
