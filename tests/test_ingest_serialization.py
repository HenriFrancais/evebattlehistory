"""schedule_ingest must never run two ingests for the same BR concurrently,
and should coalesce a burst of schedules into a single trailing re-run.

Regression: rapid source edits fired an unguarded asyncio.create_task per edit,
so two run_ingest coroutines for the same BR raced on SQLite's single writer and
died with 'database is locked' mid-ingest (aborting association and any concurrent
delete). See jobs.schedule_ingest.
"""
from __future__ import annotations

import asyncio

import pytest

import app.ingest.jobs as jobs


@pytest.fixture(autouse=True)
def _reset_jobs_state():
    jobs._reset_ingest_state_for_tests()
    yield
    jobs._reset_ingest_state_for_tests()


async def _drain() -> None:
    """Wait for all outstanding ingest tasks to finish."""
    for _ in range(1000):
        if not jobs._active_tasks:
            return
        await asyncio.gather(*list(jobs._active_tasks), return_exceptions=True)
    raise AssertionError("ingest tasks never drained")


@pytest.mark.asyncio
async def test_same_br_never_runs_concurrently(monkeypatch):
    """Five rapid schedules for one BR must not overlap in run_ingest."""
    concurrency = 0
    max_concurrency = 0
    calls = 0

    async def fake_run_ingest(settings, br_id):
        nonlocal concurrency, max_concurrency, calls
        calls += 1
        concurrency += 1
        max_concurrency = max(max_concurrency, concurrency)
        await asyncio.sleep(0.02)  # hold the "writer" so overlaps would be visible
        concurrency -= 1

    monkeypatch.setattr(jobs, "run_ingest", fake_run_ingest)

    for _ in range(5):
        jobs.schedule_ingest(object(), "br-x")  # type: ignore[arg-type]

    await _drain()

    assert max_concurrency == 1, f"ingests overlapped (max={max_concurrency})"
    # Coalesced: the in-flight run + at most one trailing re-run, not five.
    assert calls <= 2, f"expected coalescing, got {calls} runs"
    assert calls >= 1


@pytest.mark.asyncio
async def test_different_brs_do_not_overlap_writes(monkeypatch):
    """Ingests for different BRs are also serialized (one SQLite writer)."""
    concurrency = 0
    max_concurrency = 0

    async def fake_run_ingest(settings, br_id):
        nonlocal concurrency, max_concurrency
        concurrency += 1
        max_concurrency = max(max_concurrency, concurrency)
        await asyncio.sleep(0.02)
        concurrency -= 1

    monkeypatch.setattr(jobs, "run_ingest", fake_run_ingest)

    for i in range(4):
        jobs.schedule_ingest(object(), f"br-{i}")  # type: ignore[arg-type]

    await _drain()
    assert max_concurrency == 1, f"cross-BR ingests overlapped (max={max_concurrency})"


@pytest.mark.asyncio
async def test_all_scheduled_brs_eventually_ingest(monkeypatch):
    """Serialization must not drop work: every distinct BR gets ingested."""
    seen: set[str] = set()

    async def fake_run_ingest(settings, br_id):
        seen.add(br_id)
        await asyncio.sleep(0)

    monkeypatch.setattr(jobs, "run_ingest", fake_run_ingest)

    for i in range(3):
        jobs.schedule_ingest(object(), f"br-{i}")  # type: ignore[arg-type]

    await _drain()
    assert seen == {"br-0", "br-1", "br-2"}
