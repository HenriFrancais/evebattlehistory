"""Ingest pipeline: resolve all BR sources → merge → fetch → persist → aggregate."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings, get_app_config
from app.db.engine import get_sessionmaker
from app.db.models import BattleReport, BrKillmail, BrSource
from app.esi.demo import DemoEsiClient
from app.fights.aggregate import aggregate_br
from app.ingest.persist import persist_killmails
from app.ingest.sources.factory import resolve_source
from app.logs.associate import associate_logs_for_br
from app.observability.logging import log


async def run_ingest(settings: Settings, br_id: str) -> None:
    """Run the full ingest pipeline for a battle report.

    Phases:
      1. resolving  - resolve ALL BrSource rows; union killmail refs (dedup)
      2. enriching  - fetch killmail JSON + resolve names via ESI
      3. persisting - write killmails + BrKillmail links to DB (idempotent)
      4. clustering - aggregate fights, compute ISK, label sides
      5. ready      - mark complete

    Per-source error isolation: each source is resolved independently.
    If a source fails, its BrSource.status=error and its error_text is set,
    but other sources continue. If ALL sources error, BR status=error.
    If at least one source succeeds, ingest proceeds with the merged KM set.

    Back-compat: BRs created with the old single-url path have source_url set
    on BattleReport.  On such BRs, if there are no BrSource rows, a single
    link BrSource is created automatically.

    Any exception outside source resolution sets status=error.
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

            # Back-compat: ensure at least one BrSource row exists
            source_check = await session.execute(
                select(BrSource).where(BrSource.br_id == br_id)
            )
            existing_sources = list(source_check.scalars())
            if not existing_sources and br.source_url:
                fallback_src = BrSource(
                    br_id=br_id,
                    kind="link",
                    url=br.source_url,
                    status="pending",
                    km_count=0,
                    created_at=dt.datetime.now(dt.UTC),
                )
                session.add(fallback_src)
                await session.commit()
                existing_sources = [fallback_src]

            br.status = "resolving"
            br.progress_pct = 10
            br.started_at = dt.datetime.now(dt.UTC)
            await session.commit()

        log.info("pipeline.resolving", br_id=br_id)

        # Resolve each source independently; collect results + errors
        all_refs: dict[int, str] = {}  # km_id → km_hash (deduped)
        all_values: dict[int, float | None] = {}  # km_id → zkb.totalValue
        primary_resolved_source: str | None = None
        primary_source_ref: str | None = None
        primary_title: str | None = None
        any_ok = False

        # Load source rows fresh after potential creation
        async with session_maker() as session:
            src_result = await session.execute(
                select(BrSource).where(BrSource.br_id == br_id)
            )
            source_rows = list(src_result.scalars())

        for src_row in source_rows:
            source_id = src_row.source_id
            try:
                resolved = await resolve_source(
                    source_kind=src_row.kind,
                    source_url=src_row.url,
                    source_system_id=src_row.system_id,
                    source_window_start=src_row.window_start,
                    source_window_end=src_row.window_end,
                    source_label=src_row.label,
                    settings=settings,
                )

                # Merge refs (dedup by km_id — later sources can't overwrite earlier ones)
                for km_id, km_hash in resolved.refs:
                    if km_id not in all_refs:
                        all_refs[km_id] = km_hash
                        all_values[km_id] = resolved.values.get(km_id)

                # Mark source ok
                async with session_maker() as session:
                    src = (
                        await session.execute(
                            select(BrSource).where(BrSource.source_id == source_id)
                        )
                    ).scalar_one()
                    src.status = "ok"
                    src.km_count = len(resolved.refs)
                    src.error_text = None
                    await session.commit()

                # First successful source sets primary BR metadata
                if not any_ok:
                    primary_resolved_source = resolved.source
                    primary_source_ref = resolved.source_ref
                    primary_title = resolved.title

                any_ok = True

            except Exception as src_exc:
                log.warning(
                    "pipeline.source_error",
                    br_id=br_id,
                    source_id=source_id,
                    error=str(src_exc),
                )
                async with session_maker() as session:
                    src_or_none: BrSource | None = (
                        await session.execute(
                            select(BrSource).where(BrSource.source_id == source_id)
                        )
                    ).scalar_one_or_none()
                    if src_or_none is not None:
                        src_or_none.status = "error"
                        src_or_none.error_text = str(src_exc)
                        await session.commit()

        if not any_ok:
            error_msgs: list[str] = []
            async with session_maker() as session:
                failed_rows = list(
                    (
                        await session.execute(
                            select(BrSource).where(BrSource.br_id == br_id)
                        )
                    ).scalars()
                )
            error_msgs = [
                f"source {s.source_id}: {s.error_text}" for s in failed_rows if s.error_text
            ]
            raise RuntimeError("All sources failed: " + "; ".join(error_msgs))

        refs: list[tuple[int, str]] = list(all_refs.items())

        # Update BR with merged KM count and primary source metadata
        async with session_maker() as session:
            result = await session.execute(
                select(BattleReport).where(BattleReport.br_id == br_id)
            )
            br = result.scalar_one()
            if primary_resolved_source:
                br.source = primary_resolved_source
            if primary_source_ref:
                br.source_ref = primary_source_ref
            if primary_title and not br.title:
                br.title = primary_title
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
            await persist_killmails(session, killmails_json, names, values=all_values)

            # Upsert BrKillmail links (idempotent)
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
