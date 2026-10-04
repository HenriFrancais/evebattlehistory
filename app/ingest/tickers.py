"""Corporation / alliance tickers: fetched from ESI once and stored.

Tickers are shown next to every pilot name. ESI's bulk name lookup does not return
them, so each entity needs its own request; they are fetched for the entities of a
battle report when it is ingested, and for everything else by the backfill command:

    python -m app.ingest.tickers
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alliance, Corporation
from app.observability.logging import log


class TickerSource(Protocol):
    async def fetch_tickers(
        self, corp_ids: list[int], alliance_ids: list[int]
    ) -> tuple[dict[int, str], dict[int, str]]: ...


async def fill_missing_tickers(
    session: AsyncSession,
    esi: TickerSource,
    corp_ids: set[int] | None = None,
    alliance_ids: set[int] | None = None,
) -> int:
    """Fetch and store tickers for corporations / alliances that have none.

    ``corp_ids`` / ``alliance_ids`` limit the work to those entities (None = every
    row lacking a ticker). Never raises on an upstream failure — a missing ticker
    only means a name is shown without one. The caller commits. Returns the number
    of tickers stored.
    """
    corp_q = select(Corporation.corporation_id).where(Corporation.ticker.is_(None))
    if corp_ids is not None:
        corp_q = corp_q.where(Corporation.corporation_id.in_(corp_ids))
    alli_q = select(Alliance.alliance_id).where(Alliance.ticker.is_(None))
    if alliance_ids is not None:
        alli_q = alli_q.where(Alliance.alliance_id.in_(alliance_ids))
    need_corps = list((await session.execute(corp_q)).scalars())
    need_allis = list((await session.execute(alli_q)).scalars())
    if not need_corps and not need_allis:
        return 0

    try:
        corp_tickers, alli_tickers = await esi.fetch_tickers(need_corps, need_allis)
    except Exception as exc:
        log.warning("tickers.fetch_failed", error=str(exc))
        return 0

    for cid, ticker in corp_tickers.items():
        await session.execute(
            update(Corporation).where(Corporation.corporation_id == cid).values(ticker=ticker)
        )
    for aid, ticker in alli_tickers.items():
        await session.execute(
            update(Alliance).where(Alliance.alliance_id == aid).values(ticker=ticker)
        )
    filled = len(corp_tickers) + len(alli_tickers)
    log.info(
        "tickers.filled",
        filled=filled,
        wanted=len(need_corps) + len(need_allis),
    )
    return filled


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker, init_models
    from app.esi.client import get_esi_client

    async def _main() -> None:
        settings = get_settings()
        await init_models(settings)  # apply pending schema migrations first
        async with get_sessionmaker(settings)() as session:
            n = await fill_missing_tickers(session, get_esi_client(settings))
            await session.commit()
        print(f"stored {n} tickers")

    asyncio.run(_main())
