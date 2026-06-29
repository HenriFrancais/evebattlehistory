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
* **Time-window association (E1)**: association is based on time-window overlap
  only.  Killmail participation is NOT a filter — it becomes a flag surfaced via
  ``br_participants``.  This ensures logistics/links/support characters whose
  combat-effect logs overlap a fight window get their events stamped even if they
  never appeared on a killmail.  Effect events are self-limiting: a character
  elsewhere produces few/no in-window combat lines; deliberate log uploads are the
  intentional signal.

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
from app.observability.logging import log


def _floor_to_bucket(ts: dt.datetime, bucket_seconds: int = BUCKET_SECONDS) -> dt.datetime:
    """Floor *ts* down to the nearest *bucket_seconds* boundary (UTC-aware).

    Log event timestamps are UTC, but SQLite returns them tz-naive. Calling
    ``.timestamp()`` on a naive datetime makes Python interpret it in the
    server's LOCAL timezone — which silently shifts every bucket when the server
    is not on UTC (e.g. BST = UTC+1 shifts buckets -1h). Normalise to UTC first.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.UTC)
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


async def _session_superseded_file_ids(
    session: AsyncSession,
    character_id: int,
    session_started_at: dt.datetime | None,
) -> set[int]:
    """Return the file_ids of a logical session that are SUPERSEDED (non-canonical).

    A "logical session" is identified by (claimed_character_id, session_started_at).
    A player often uploads the same client log more than once while it is still
    growing (mid-fight, then again at the end); each later upload is a superset of
    the earlier one but has different content, so the sha256 store dedupe does not
    catch it.  Left alone, every overlapping event is stamped to the fight twice and
    all amount effects double-count.

    The CANONICAL file is the most complete one — ordered by (log_end_at, event_count,
    file_id) descending — and is the only one allowed to keep its fight stamps.  Every
    other parsed file in the group is returned here so the caller can un-stamp it.

    Returns an empty set when ``session_started_at`` is None (we cannot group reliably)
    or when the group has a single file — i.e. no supersession applies.
    """
    if session_started_at is None:
        return set()
    rows = (
        await session.execute(
            select(
                GamelogFile.file_id,
                GamelogFile.log_end_at,
                GamelogFile.event_count,
            ).where(
                GamelogFile.claimed_character_id == character_id,
                GamelogFile.session_started_at == session_started_at,
                GamelogFile.parse_status == "parsed",
            )
        )
    ).all()
    if len(rows) <= 1:
        return set()

    # Canonical = most complete file: latest log_end_at, then most events, then file_id.
    def _coverage_key(row: tuple[int, dt.datetime | None, int]) -> tuple[float, int, int]:
        file_id, log_end_at, event_count = row
        end_epoch = log_end_at.replace(tzinfo=dt.UTC).timestamp() if log_end_at else 0.0
        return (end_epoch, event_count or 0, file_id)

    triples: list[tuple[int, dt.datetime | None, int]] = [(r[0], r[1], r[2]) for r in rows]
    canonical_id = max(triples, key=_coverage_key)[0]
    return {fid for fid, _, _ in triples if fid != canonical_id}


async def _unstamp_files(
    session: AsyncSession,
    file_ids: set[int],
    character_id: int,
) -> set[tuple[int, int]]:
    """Clear fight_id on the given files' events; return (fight_id, character_id) pairs
    that must be rebuilt because they lost contributions.
    """
    if not file_ids:
        return set()
    pairs: set[tuple[int, int]] = set()
    old_fight_ids = (
        await session.execute(
            select(LogEvent.fight_id)
            .where(LogEvent.file_id.in_(file_ids))
            .where(LogEvent.fight_id.is_not(None))
            .distinct()
        )
    ).scalars()
    for fid in old_fight_ids:
        if fid is not None:
            pairs.add((fid, character_id))
    await session.execute(
        update(LogEvent).where(LogEvent.file_id.in_(file_ids)).values(fight_id=None),
        execution_options={"synchronize_session": False},
    )
    return pairs


_EWAR_DEDUPE_TYPES = ("scram", "disrupt")


async def _dedupe_ewar_relationships(session: AsyncSession, fight_ids: set[int]) -> None:
    """Collapse duplicate tackle observations seen across multiple logs.

    One physical tackle can be logged by the tackler (authoritative), the target,
    and any number of third-party observers. For each
    (fight_id, source_name, target_name, effect_type) group, sort observations by ts
    and greedily cluster: an observation joins the current cluster if its ts is within
    BUCKET_SECONDS of the cluster's anchor; start a new cluster otherwise.  Within each
    cluster keep ONE representative (prefer authoritative=True, then lowest event_id) and
    set dedupe_suppressed=True on the rest.

    This collapses straddling duplicates (e.g. 4s and 6s apart) that bucket-boundary
    alignment would miss, while keeping genuinely distinct re-tackles (e.g. 30s apart)
    as separate events.

    Reversible (no deletes) → idempotent under re-association/reparse.
    """
    if not fight_ids:
        return

    # Reset suppression for these fights so the pass is fully recomputable.
    await session.execute(
        update(LogEvent)
        .where(
            LogEvent.fight_id.in_(list(fight_ids)),
            LogEvent.effect_type.in_(list(_EWAR_DEDUPE_TYPES)),
        )
        .values(dedupe_suppressed=False)
    )

    rows = (
        await session.execute(
            select(
                LogEvent.event_id,
                LogEvent.fight_id,
                LogEvent.ts,
                LogEvent.source_name,
                LogEvent.target_name,
                LogEvent.effect_type,
                LogEvent.authoritative,
            ).where(
                LogEvent.fight_id.in_(list(fight_ids)),
                LogEvent.effect_type.in_(list(_EWAR_DEDUPE_TYPES)),
            )
        )
    ).all()

    # Group by (fight_id, source_name, target_name, effect_type) — no bucket floor.
    groups_by_key: dict[
        tuple[int, str | None, str | None, str], list[tuple[int, float, bool]]
    ] = defaultdict(list)
    for event_id, fid, ts, src, tgt, etype, auth in rows:
        # Normalise ts to a float epoch for comparison.
        ts_epoch: float
        if ts.tzinfo is None:
            ts_epoch = ts.replace(tzinfo=dt.UTC).timestamp()
        else:
            ts_epoch = ts.timestamp()
        groups_by_key[(fid, src, tgt, etype)].append((event_id, ts_epoch, bool(auth)))

    suppress_ids: list[int] = []
    window = float(BUCKET_SECONDS)

    for members in groups_by_key.values():
        if len(members) < 2:
            continue
        # Sort by timestamp so the greedy clustering is deterministic.
        members.sort(key=lambda m: m[1])

        # Greedy clustering: each cluster has one anchor timestamp.
        # An observation joins if its ts is within BUCKET_SECONDS of the anchor.
        clusters: list[list[tuple[int, float, bool]]] = []
        for event_id, ts_epoch, auth in members:
            placed = False
            for cluster in clusters:
                anchor_ts = cluster[0][1]
                if ts_epoch - anchor_ts <= window:
                    cluster.append((event_id, ts_epoch, auth))
                    placed = True
                    break
            if not placed:
                clusters.append([(event_id, ts_epoch, auth)])

        # Within each cluster keep the best representative; suppress the rest.
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            # Sort: authoritative first (True > False when negated), then lowest event_id.
            cluster.sort(key=lambda m: (not m[2], m[0]))
            suppress_ids.extend(eid for eid, _, _ in cluster[1:])

    if suppress_ids:
        await session.execute(
            update(LogEvent)
            .where(LogEvent.event_id.in_(suppress_ids))
            .values(dedupe_suppressed=True)
        )
    await session.flush()


async def associate_file(
    session: AsyncSession,
    file_id: int,
    pad_seconds: int = 120,
    *,
    dedupe_tackle: bool = True,
) -> int:
    """Stamp LogEvent.fight_id for events owned by *file_id*, rebuild buckets.

    Algorithm
    ---------
    1. Load the GamelogFile; skip if character_id is None or timestamps absent.
    2. Idempotent reset: clear fight_id on this file's events, collect the
       (fight_id, character_id) pairs that *used to* have contributions from
       this file so we can rebuild their buckets.
    3. Find candidate fights by TIME-WINDOW OVERLAP only: a fight is a candidate
       if its padded window [started_at-pad, ended_at+pad] overlaps the file's
       [log_start_at, log_end_at].  Killmail participation is NOT required here;
       it becomes a flag surfaced via br_participants (E1 design change).
    4. Stamp fight_id on events within each candidate fight's padded window.
    5. Rebuild buckets for every (fight_id, character_id) pair touched.

    ``dedupe_tackle=False`` skips the per-fight tackle dedupe pass — for bulk callers
    (reparse) that process many files touching the same fights, so the O(files) repeated
    full-fight dedupe is replaced by a single pass the caller runs once at the end.

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
    session_started_at: dt.datetime | None = file_row.session_started_at

    if character_id is None or log_start is None or log_end is None:
        log.info(
            "associate_file.skip_unresolved",
            file_id=file_id,
            character_id=character_id,
        )
        return 0

    # 1b. Supersession: within a logical session (same character + session_started_at),
    # a player may upload the still-growing client log more than once.  Only the most
    # complete file contributes events to fights; un-stamp every superseded sibling so
    # overlapping events are not double-counted.  Done first so the canonical/superseded
    # decision is independent of the order files happen to be associated in.
    superseded_ids = await _session_superseded_file_ids(
        session, character_id, session_started_at
    )
    rebuild_pairs: set[tuple[int, int]] = await _unstamp_files(
        session, superseded_ids, character_id
    )

    if file_id in superseded_ids:
        # This file is not canonical — it contributes nothing.  Its events were just
        # un-stamped above; rebuild the buckets it (and any sibling) used to feed.
        await _rebuild_buckets_for_pairs(session, rebuild_pairs)
        if dedupe_tackle:
            await _dedupe_ewar_relationships(session, {fid for fid, _ in rebuild_pairs})
        log.info(
            "associate_file.superseded",
            file_id=file_id,
            character_id=character_id,
            session_started_at=str(session_started_at),
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

    # Clear fight_id on all this file's events.
    # synchronize_session=False: skip in-Python identity-map evaluation so we
    # avoid TypeError when freshly-added LogEvent objects have tz-aware ts but
    # the fight window bounds are tz-naive (as SQLite returns them). Defense-in-
    # depth alongside fix #1 (naive ts from _parse_ts).
    await session.execute(
        update(LogEvent).where(LogEvent.file_id == file_id).values(fight_id=None),
        execution_options={"synchronize_session": False},
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
    # All fights whose padded window overlaps the file's log window are candidates.
    # Killmail participation is not required — it is a flag, not a filter.
    matched_fights: list[Fight] = list(fight_result.scalars())

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
            .values(fight_id=fight.fight_id),
            execution_options={"synchronize_session": False},
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

    # 5. Rebuild buckets for all affected pairs (old + new, plus any superseded sibling
    # whose stamps we just cleared so its now-orphaned buckets are recomputed too).
    all_pairs = (
        {(fid, character_id) for fid in old_fight_ids} | new_pairs | rebuild_pairs
    )
    await _rebuild_buckets_for_pairs(session, all_pairs)

    # 6. Dedupe tackle relationships for all fights touched by this file
    if dedupe_tackle:
        await _dedupe_ewar_relationships(session, {fid for fid, _ in all_pairs})

    log.info(
        "associate_file.done",
        file_id=file_id,
        character_id=character_id,
        fights_matched=len(matched_fights),
        events_stamped=stamped_total,
    )
    return stamped_total


async def associate_logs_for_br(session: AsyncSession, br_id: str) -> None:
    """Associate all uploaded files whose time range overlaps any of *br_id*'s fights.

    Called after aggregate_br in the ingest pipeline.  Each file is individually
    guarded so one bad file does not abort association of the remaining files.
    The set of file_ids to process is collected upfront (outside the per-file guard)
    so lookup failures still propagate; only associate_file failures are swallowed.

    E1 change: we no longer filter to killmail participants.  Instead, we find all
    parsed GamelogFiles whose [log_start_at, log_end_at] overlaps the padded window
    of ANY fight in this BR.  ``associate_file`` then applies the per-fight overlap
    rule at event-stamp time.  This ensures logistics/links whose logs overlap a fight
    window are processed even if they never appeared on a killmail.
    """
    import datetime as dt

    PAD = dt.timedelta(seconds=120)

    # Collect fights for this BR
    fights_result = await session.execute(
        select(Fight)
        .join(BrFight, BrFight.fight_id == Fight.fight_id)
        .where(BrFight.br_id == br_id)
    )
    fights = list(fights_result.scalars())

    if not fights:
        log.info("associate_logs_for_br.no_fights", br_id=br_id)
        return

    # Find all parsed GamelogFiles whose log window overlaps ANY fight's padded window.
    # A file overlaps fight F iff: file.log_end_at >= F.started_at-PAD  AND
    #                                file.log_start_at <= F.ended_at+PAD
    # We build a union by collecting file_ids for each fight.
    files_to_associate: set[int] = set()
    for fight in fights:
        padded_start = fight.started_at - PAD
        padded_end = fight.ended_at + PAD
        file_result = await session.execute(
            select(GamelogFile.file_id).where(
                GamelogFile.parse_status == "parsed",
                GamelogFile.log_end_at >= padded_start,
                GamelogFile.log_start_at <= padded_end,
                GamelogFile.claimed_character_id.is_not(None),
            )
        )
        files_to_associate.update(file_result.scalars())

    log.info(
        "associate_logs_for_br.start",
        br_id=br_id,
        fight_count=len(fights),
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

    # Dedupe tackle relationships across all files for this BR's fights.
    # associate_file already dedupes per-file, but now all files are processed
    # so we do a final pass over all fights to collapse cross-file duplicates.
    await _dedupe_ewar_relationships(session, {f.fight_id for f in fights})


async def associate_file_to_all(session: AsyncSession, file_id: int) -> None:
    """Associate a freshly-uploaded file against all existing fights.

    Called after a successful, resolved log upload.  Guarded: association
    failure is logged but does not propagate.
    """
    try:
        await associate_file(session, file_id)
    except Exception as exc:
        log.error("associate_file_to_all.failed", file_id=file_id, error=str(exc))
