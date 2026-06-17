"""Ingest pipeline: resolve BR → fetch killmails → persist → aggregate."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings, get_app_config
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport, BrKillmail
from app.esi.demo import DemoEsiClient
from app.fights.aggregate import aggregate_br
from app.ingest.persist import persist_killmails
from app.ingest.sources.factory import get_source
from app.logs.associate import associate_logs_for_br
from app.observability.logging import log


async def run_ingest(settings: Settings, br_id: str) -> None:
    """Run the full ingest pipeline for a battle report.

    Phases:
      1. resolving  - call BR source to get killmail refs
      2. enriching  - fetch killmail JSON + resolve names via ESI
      3. persisting - write killmails + BrKillmail links to DB
      4. clustering - aggregate fights, compute ISK, label sides
      5. ready      - mark complete

    Any exception sets status=error and suppresses the raise so the caller
    (a background Task) never sees an unhandled exception.
    """
    session_maker = get_sessionmaker(settings)

    try:
        # ------------------------------------------------------------------ #
        # Phase 1: resolving                                                  #
        # ------------------------------------------------------------------ #
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one_or_none()
            if br is None:
                log.error("pipeline.br_not_found", br_id=br_id)
                return
            source_url: str = br.source_url
            br.status = "resolving"
            br.progress_pct = 10
            br.started_at = dt.datetime.now(dt.UTC)
            await session.commit()

        log.info("pipeline.resolving", br_id=br_id)
        source = get_source(source_url, settings)
        resolved = await source.resolve(source_url)
        refs = resolved.refs

        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            br.source = resolved.source
            br.source_ref = resolved.source_ref
            if resolved.title and not br.title:
                br.title = resolved.title
            br.km_count = len(refs)
            br.progress_pct = 25
            await session.commit()

        # ------------------------------------------------------------------ #
        # Phase 2: enriching                                                  #
        # ------------------------------------------------------------------ #
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            br.status = "enriching"
            br.progress_pct = 40
            await session.commit()

        log.info("pipeline.enriching", br_id=br_id, km_count=len(refs))

        if settings.data_source == "demo":
            esi: DemoEsiClient = DemoEsiClient(settings.demo_data_dir)
        else:
            from app.esi.client import get_esi_client

            esi = get_esi_client(settings)  # type: ignore[assignment]

        killmails_json = await esi.fetch_killmails(refs)

        # Collect all IDs that need name resolution
        ids_to_resolve: set[int] = set()
        for km in killmails_json:
            victim = km.get("victim", {})
            if isinstance(victim, dict):
                for field in ("character_id", "corporation_id", "alliance_id", "ship_type_id"):
                    val = victim.get(field)
                    if isinstance(val, int):
                        ids_to_resolve.add(val)
            sys_id = km.get("solar_system_id")
            if isinstance(sys_id, int):
                ids_to_resolve.add(sys_id)
            raw_attackers = km.get("attackers")
            attackers_list = raw_attackers if isinstance(raw_attackers, list) else []
            for att in attackers_list:
                if isinstance(att, dict):
                    for field in (
                        "character_id",
                        "corporation_id",
                        "alliance_id",
                        "ship_type_id",
                        "weapon_type_id",
                    ):
                        val = att.get(field)
                        if isinstance(val, int):
                            ids_to_resolve.add(val)

        names = await esi.resolve_names(list(ids_to_resolve))

        # ------------------------------------------------------------------ #
        # Phase 3: persisting                                                 #
        # ------------------------------------------------------------------ #
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            br.status = "persisting"
            br.progress_pct = 60
            await session.commit()

        log.info("pipeline.persisting", br_id=br_id)

        async with session_maker() as session:
            await persist_killmails(session, killmails_json, names)

            # Upsert BrKillmail links
            km_ids = [
                int(str(km["killmail_id"]))
                for km in killmails_json
                if "killmail_id" in km
            ]
            if km_ids:
                brkm_rows = [{"br_id": br_id, "killmail_id": kid} for kid in km_ids]
                stmt = sqlite_insert(BrKillmail).values(brkm_rows)
                stmt = stmt.on_conflict_do_nothing(index_elements=["br_id", "killmail_id"])
                await session.execute(stmt)

            await session.commit()

        # ------------------------------------------------------------------ #
        # Phase 4: clustering                                                 #
        # ------------------------------------------------------------------ #
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            br.status = "clustering"
            br.progress_pct = 80
            await session.commit()

        log.info("pipeline.clustering", br_id=br_id)

        app_config = get_app_config()
        async with session_maker() as session:
            await aggregate_br(
                session,
                br_id=br_id,
                our_alliance_ids=app_config.our_alliance_ids,
                our_corp_ids=app_config.our_corp_ids,
            )
            # Phase 4.5: associate uploaded logs to fights for this BR.
            # Guarded inside associate_logs_for_br — failure never fails the ingest.
            await associate_logs_for_br(session, br_id)
            await session.commit()

        # ------------------------------------------------------------------ #
        # Phase 5: ready                                                      #
        # ------------------------------------------------------------------ #
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            br.status = "ready"
            br.progress_pct = 100
            br.completed_at = dt.datetime.now(dt.UTC)
            await session.commit()

        log.info("pipeline.ready", br_id=br_id)

    except Exception as exc:
        log.error("pipeline.error", br_id=br_id, error=str(exc))
        try:
            async with session_maker() as session:
                result = await session.execute(
                    select(BattleReport).where(BattleReport.br_id == br_id)
                )
                br = result.scalar_one_or_none()
                if br is not None:
                    br.status = "error"
                    br.error_text = str(exc)
                    await session.commit()
        except Exception as inner_exc:
            log.error("pipeline.error_update_failed", br_id=br_id, error=str(inner_exc))
