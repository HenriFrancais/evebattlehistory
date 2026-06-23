"""Recompute stored BR outcomes (our ISK destroyed/lost, efficiency, win/tie/loss).

``BattleReport.result`` and its ISK rollups are persisted at ingest time, so a
change to the classification thresholds (see :data:`app.fights.outcomes.WIN_THRESHOLD`
/ ``TIE_THRESHOLD``) or to side overrides does not retroactively touch existing
rows. This module re-derives every BR from its current per-entity side
allocation via :func:`recompute_br_outcome` and writes the fresh values back.

Idempotent and safe to re-run. Run as a one-off after a threshold change::

    python -m app.analytics.recompute
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sides_config import recompute_br_outcome
from app.db.models import BattleReport
from app.observability.logging import log


async def recompute_all_brs(
    session: AsyncSession,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
) -> tuple[int, int]:
    """Recompute outcomes for every battle report.

    Returns ``(total, changed)`` where ``changed`` counts BRs whose stored
    win/tie/loss ``result`` differed from the freshly computed one. The caller
    commits.
    """
    rows = (
        await session.execute(select(BattleReport.br_id, BattleReport.result))
    ).all()

    changed = 0
    for br_id, old_result in rows:
        out = await recompute_br_outcome(
            session,
            br_id,
            baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps,
        )
        new_result = out["result"]
        if new_result != old_result:
            changed += 1
            log.info(
                "recompute.result_changed",
                br_id=br_id,
                old=old_result,
                new=new_result,
            )

    log.info("recompute.done", total=len(rows), changed=changed)
    return len(rows), changed


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker

    async def _main() -> None:
        settings = get_settings()
        cfg = get_app_config()
        async with get_sessionmaker(settings)() as session:
            total, changed = await recompute_all_brs(
                session,
                baseline_alliances=set(cfg.our_alliance_ids),
                baseline_corps=set(cfg.our_corp_ids),
            )
            await session.commit()
        print(f"recomputed {total} battle reports; {changed} result(s) changed")

    asyncio.run(_main())
