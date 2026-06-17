"""Log↔fight association and read-optimised bucket builder.

Design
------
* **Idempotent**: re-running for the same file or the same BR produces
  identical fight_ids and bucket sums (no duplicates).
* **Order-independent**: the BR may be ingested before or after the log
  is uploaded; both ``associate_logs_for_br`` and ``associate_file_to_all``
  converge to the same result.
* **Bucket rebuild strategy**: when a file is re-associated, we delete all
  LogEventBucket rows for every (fight_id, character_id) pair that file
  *used to* contribute to, then rebuild each pair from *all* LogEvent rows
  with that fight_id+character_id — not just from this file.  This keeps
  buckets correct regardless of which files were uploaded and in what order.

Public API
----------
    stamped = await associate_file(session, file_id, pad_seconds=120)
    await associate_logs_for_br(session, br_id)
    await associate_file_to_all(session, file_id)
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BUCKET_SECONDS,
    BrFight,
    Fight,
    GamelogFile,
    LogEvent,
    LogEventBucket,
)
from app.fights.participants import fight_participant_char_ids
from app.observability.logging import log


def _floor_to_bucket(ts: dt.datetime, bucket_seconds: int = BUCKET_SECONDS) -> dt.datetime:
    """Floor *ts* down to the nearest *bucket_seconds* boundary (UTC-aware)."""
    epoch = ts.timestamp()
    floored = (epoch // bucket_seconds) * bucket_seconds
    return dt.datetime.fromtimestamp(floored, tz=dt.UTC)


async def _rebuild_buckets_for_pairs(
    session: AsyncSession,
    pairs: set[tuple[int, int]],
) -> None:
    """Delete and rebuild LogEventBucket rows for the given (fight_id, character_id) pairs.

    Reads ALL LogEvent rows for each pair (not just from one file) so the
    result is independent of upload order.
    """
    if not pairs:
        return

    for fight_id, character_id in pairs:
        # Delete stale buckets for this pair
        await session.execute(
            delete(LogEventBucket).where(
                LogEventBucket.fight_id == fight_id,
                LogEventBucket.character_id == character_id,
            )
        )

        # Load all events for this (fight_id, character_id)
        result = await session.execute(
            select(
                LogEvent.ts,
                LogEvent.effect_type,
                LogEvent.direction,
                LogEvent.amount,
            ).where(
                LogEvent.fight_id == fight_id,
                LogEvent.character_id == character_id,
            )
        )
        rows = result.fetchall()

        # Aggregate into buckets
        # key: (bucket_ts, effect_type, direction)
        buckets: dict[tuple[dt.datetime, str, str], tuple[float, int]] = defaultdict(
            lambda: (0.0, 0)
        )
        for ts, effect_type, direction, amount in rows:
            bucket_ts = _floor_to_bucket(ts)
            # NULL → "" coercion: effect_type and direction are PK columns in
            # LogEventBucket so they cannot be NULL (SQLite constraint).  Use ""
            # to represent "unknown/none".  Phase 3/4 readers must treat "" as None.
            etype = effect_type or ""
            dire = direction or ""
            prev_sum, prev_cnt = buckets[(bucket_ts, etype, dire)]
            buckets[(bucket_ts, etype, dire)] = (
                prev_sum + (amount or 0.0),
                prev_cnt + 1,
            )

        # Insert new bucket rows
        for (bucket_ts, etype, dire), (sum_amount, event_count) in buckets.items():
            session.add(
                LogEventBucket(
                    fight_id=fight_id,
                    character_id=character_id,
                    bucket_ts=bucket_ts,
                    effect_type=etype,
                    direction=dire,
                    sum_amount=sum_amount,
                    event_count=event_count,
                )
            )

    await session.flush()


async def associate_file(
    session: AsyncSession,
    file_id: int,
    pad_seconds: int = 120,
) -> int:
    """Stamp LogEvent.fight_id for events owned by *file_id*, rebuild buckets.

    Algorithm
    ---------
    1. Load the GamelogFile; skip if character_id is None or timestamps absent.
    2. Idempotent reset: clear fight_id on this file's events, collect the
       (fight_id, character_id) pairs that *used to* have contributions from
       this file so we can rebuild their buckets.
    3. Find candidate fights: the file's character participated AND the fight
       window (with padding) overlaps the file's log window.
    4. Stamp fight_id on events within each candidate fight's padded window.
    5. Rebuild buckets for every (fight_id, character_id) pair touched.

    Returns
    -------
    int
        Number of LogEvent rows stamped with a fight_id.
    """
    # 1. Load the file
    file_row = (
        await session.execute(select(GamelogFile).where(GamelogFile.file_id == file_id))
    ).scalar_one_or_none()

    if file_row is None:
        log.warning("associate_file.file_not_found", file_id=file_id)
        return 0

    character_id: int | None = file_row.claimed_character_id
    log_start: dt.datetime | None = file_row.log_start_at
    log_end: dt.datetime | None = file_row.log_end_at

    if character_id is None or log_start is None or log_end is None:
        log.info(
            "associate_file.skip_unresolved",
            file_id=file_id,
            character_id=character_id,
        )
        return 0

    # 2. Idempotent reset: collect old (fight_id, character_id) pairs from THIS file
    old_fight_ids_result = await session.execute(
        select(LogEvent.fight_id)
        .where(LogEvent.file_id == file_id)
        .where(LogEvent.fight_id.is_not(None))
        .distinct()
    )
    old_fight_ids: set[int] = {fid for fid in old_fight_ids_result.scalars() if fid is not None}

    # Clear fight_id on all this file's events
    await session.execute(
        update(LogEvent).where(LogEvent.file_id == file_id).values(fight_id=None)
    )

    # 3. Find candidate fights where this character participated and windows overlap.
    # SQLite does not support column-level timedelta arithmetic, so we compute the
    # padded log bounds in Python and compare them against the raw fight timestamps.
    pad = dt.timedelta(seconds=pad_seconds)
    # A fight [started_at, ended_at] overlaps the padded log window iff:
    #   fight.started_at <= log_end + pad  AND  fight.ended_at >= log_start - pad
    log_start_padded = log_start - pad
    log_end_padded = log_end + pad
    fight_result = await session.execute(
        select(Fight).where(
            Fight.started_at <= log_end_padded,
            Fight.ended_at >= log_start_padded,
        )
    )
    candidate_fights = list(fight_result.scalars())

    # Filter to fights where this character participated
    matched_fights: list[Fight] = []
    for fight in candidate_fights:
        participants = await fight_participant_char_ids(session, fight.fight_id)
        if character_id in participants:
            matched_fights.append(fight)

    # 4. Stamp fight_id on events within each fight's padded window.
    # The bounds are plain Python datetimes so the comparison is handled by
    # SQLAlchemy binding them as parameters (works fine on SQLite).
    stamped_total = 0
    new_pairs: set[tuple[int, int]] = set()

    for fight in matched_fights:
        window_start = fight.started_at - pad
        window_end = fight.ended_at + pad

        await session.execute(
            update(LogEvent)
            .where(LogEvent.file_id == file_id)
            .where(LogEvent.ts >= window_start)
            .where(LogEvent.ts <= window_end)
            .where(LogEvent.fight_id.is_(None))  # don't overwrite if already stamped
            .values(fight_id=fight.fight_id)
        )

        # Count how many were stamped in this fight
        stamped_total += (
            await session.execute(
                select(func.count())
                .select_from(LogEvent)
                .where(LogEvent.file_id == file_id)
                .where(LogEvent.fight_id == fight.fight_id)
            )
        ).scalar_one()
        new_pairs.add((fight.fight_id, character_id))

    # 5. Rebuild buckets for all affected pairs (old + new)
    all_pairs = {(fid, character_id) for fid in old_fight_ids} | new_pairs
    await _rebuild_buckets_for_pairs(session, all_pairs)

    log.info(
        "associate_file.done",
        file_id=file_id,
        character_id=character_id,
        fights_matched=len(matched_fights),
        events_stamped=stamped_total,
    )
    return stamped_total


async def associate_logs_for_br(session: AsyncSession, br_id: str) -> None:
    """Associate all uploaded files for characters that participated in *br_id*'s fights.

    Called after aggregate_br in the ingest pipeline.  Each file is individually
    guarded so one bad file does not abort association of the remaining files.
    The set of file_ids to process is collected upfront (outside the per-file guard)
    so lookup failures still propagate; only associate_file failures are swallowed.
    """
    # Collect fight_ids for this BR
    fight_id_result = await session.execute(
        select(BrFight.fight_id).where(BrFight.br_id == br_id)
    )
    fight_ids = list(fight_id_result.scalars())

    if not fight_ids:
        log.info("associate_logs_for_br.no_fights", br_id=br_id)
        return

    # For each fight, find participant char_ids, then find uploaded files
    files_to_associate: set[int] = set()
    for fight_id in fight_ids:
        participants = await fight_participant_char_ids(session, fight_id)
        if not participants:
            continue
        file_result = await session.execute(
            select(GamelogFile.file_id).where(
                GamelogFile.claimed_character_id.in_(list(participants)),
                GamelogFile.parse_status == "parsed",
            )
        )
        files_to_associate.update(file_result.scalars())

    log.info(
        "associate_logs_for_br.start",
        br_id=br_id,
        fight_count=len(fight_ids),
        file_count=len(files_to_associate),
    )

    # Per-file guard: one bad file must not drop the rest (mirrors associate_file_to_all).
    for file_id in files_to_associate:
        try:
            await associate_file(session, file_id)
        except Exception as exc:
            log.error(
                "associate_logs_for_br.file_failed",
                br_id=br_id,
                file_id=file_id,
                error=str(exc),
            )


async def associate_file_to_all(session: AsyncSession, file_id: int) -> None:
    """Associate a freshly-uploaded file against all existing fights.

    Called after a successful, resolved log upload.  Guarded: association
    failure is logged but does not propagate.
    """
    try:
        await associate_file(session, file_id)
    except Exception as exc:
        log.error("associate_file_to_all.failed", file_id=file_id, error=str(exc))
