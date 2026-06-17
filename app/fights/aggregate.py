"""BR-level fight aggregation orchestrator.

This is the only module in app/fights/ that touches the database.

Flow:
  1. Load the BR's killmails (via br_killmail join).
  2. Cluster kills into fights (cluster_kills).
  3. For each fight: assign sides, compute outcomes, label sides.
  4. Persist Fight / FightSide / FightKill / BrFight / fight_ship_count rows.
  5. Roll up BR-level ISK and result onto BattleReport.
  6. Build br_ship_count rollup.

Idempotent: clears previously derived rows for this BR before inserting.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BattleReport,
    BrFight,
    BrKillmail,
    BrShipCount,
    Fight,
    FightKill,
    FightShipCount,
    FightSide,
    Killmail,
    KillmailAttacker,
)
from app.fights.cluster import FightCluster, cluster_kills
from app.fights.labelling import SideInfo, label_sides
from app.fights.outcomes import SideStats, classify_br_result, compute_fight_sides
from app.fights.sides import SideResult, assign_sides
from app.observability.logging import log


async def _load_killmails(session: AsyncSession, br_id: str) -> list[Killmail]:
    """Load all Killmail rows linked to this BR."""
    brkm_result = await session.execute(
        select(BrKillmail.killmail_id).where(BrKillmail.br_id == br_id)
    )
    km_ids = list(brkm_result.scalars())
    if not km_ids:
        return []

    km_result = await session.execute(
        select(Killmail).where(Killmail.killmail_id.in_(km_ids))
    )
    return list(km_result.scalars())


async def _load_attackers(
    session: AsyncSession, km_ids: list[int]
) -> dict[int, list[KillmailAttacker]]:
    """Load all attackers for the given killmail IDs."""
    if not km_ids:
        return {}
    result = await session.execute(
        select(KillmailAttacker).where(KillmailAttacker.killmail_id.in_(km_ids))
    )
    attackers = list(result.scalars())
    by_km: dict[int, list[KillmailAttacker]] = defaultdict(list)
    for att in attackers:
        by_km[att.killmail_id].append(att)
    return dict(by_km)


class _KillWrapper:
    """Wraps a Killmail row with its attackers to satisfy the Protocol interfaces."""

    def __init__(self, km: Killmail, attackers: list[KillmailAttacker]) -> None:
        self.killmail_id = km.killmail_id
        self.solar_system_id = km.solar_system_id
        self.killmail_time = km.killmail_time
        self.victim_character_id = km.victim_character_id
        self.victim_alliance_id = km.victim_alliance_id
        self.victim_corporation_id = km.victim_corporation_id
        self.victim_ship_type_id = km.victim_ship_type_id
        self.total_value: float | None = (
            float(km.total_value) if km.total_value is not None else None
        )
        self.attackers: list[_AttackerWrapper] = [_AttackerWrapper(a) for a in attackers]


class _AttackerWrapper:
    """Wraps a KillmailAttacker row to satisfy the Protocol interfaces."""

    def __init__(self, att: KillmailAttacker) -> None:
        self.character_id = att.character_id
        self.corporation_id = att.corporation_id
        self.alliance_id = att.alliance_id
        self.ship_type_id = att.ship_type_id


async def _clear_derived_rows(session: AsyncSession, br_id: str) -> None:
    """Delete all derived rows for this BR so re-aggregation is idempotent."""
    fight_id_result = await session.execute(
        select(BrFight.fight_id).where(BrFight.br_id == br_id)
    )
    fight_ids = list(fight_id_result.scalars())

    if fight_ids:
        await session.execute(
            delete(FightShipCount).where(FightShipCount.fight_id.in_(fight_ids))
        )
        await session.execute(
            delete(FightKill).where(FightKill.fight_id.in_(fight_ids))
        )
        await session.execute(
            delete(FightSide).where(FightSide.fight_id.in_(fight_ids))
        )
        await session.execute(
            delete(Fight).where(Fight.fight_id.in_(fight_ids))
        )

    await session.execute(delete(BrFight).where(BrFight.br_id == br_id))
    await session.execute(delete(BrShipCount).where(BrShipCount.br_id == br_id))


def _build_label_input(
    per_side_stats: dict[int, SideStats],
    side_result: SideResult,
) -> dict[int, SideInfo]:
    """Build the per_side dict for label_sides from SideStats + SideResult."""
    all_side_idxs = set(side_result.alliance_sides.values()) | set(per_side_stats.keys())
    label_input: dict[int, SideInfo] = {
        idx: {
            "alliance_ids": per_side_stats[idx].alliance_ids
            if idx in per_side_stats
            else set(),
            "corp_ids": per_side_stats[idx].corp_ids
            if idx in per_side_stats
            else set(),
        }
        for idx in all_side_idxs
    }
    # Ensure alliance_ids from the graph are represented (in case they're
    # not in per_side_stats because all kills were on one side)
    for aid, sidx in side_result.alliance_sides.items():
        label_input.setdefault(sidx, {"alliance_ids": set(), "corp_ids": set()})
        label_input[sidx]["alliance_ids"].add(aid)
    return label_input


async def aggregate_br(
    session: AsyncSession,
    br_id: str,
    our_alliance_ids: list[int],
    our_corp_ids: list[int],
) -> None:
    """Orchestrate fight analysis and persist derived tables for one BR.

    Idempotent: clears previously derived rows before inserting.
    """
    our_alliance_set = set(our_alliance_ids)
    our_corp_set = set(our_corp_ids)

    log.info("aggregate_br.start", br_id=br_id)

    # 1. Clear old derived rows
    await _clear_derived_rows(session, br_id)

    # 2. Load killmails
    killmails = await _load_killmails(session, br_id)
    if not killmails:
        log.info("aggregate_br.no_killmails", br_id=br_id)
        return

    km_ids = [km.killmail_id for km in killmails]
    attackers_by_km = await _load_attackers(session, km_ids)

    wrapped = [
        _KillWrapper(km, attackers_by_km.get(km.killmail_id, []))
        for km in killmails
    ]

    # 3. Cluster into fights
    clusters: list[FightCluster] = cluster_kills(wrapped)  # type: ignore[arg-type]

    # 4. Process each fight cluster
    br_our_destroyed = 0.0
    br_our_lost = 0.0
    fight_seq = 0
    earliest_start: dt.datetime | None = None

    br_ship_accumulator: dict[tuple[str, int], int] = defaultdict(int)

    for cluster in clusters:
        cluster_ids = set(cluster.killmail_ids)
        cluster_wrapped = [w for w in wrapped if w.killmail_id in cluster_ids]

        # Assign sides
        side_result: SideResult = assign_sides(cluster_wrapped)  # type: ignore[arg-type]
        side_for_alliance = dict(side_result.alliance_sides)

        # Compute per-side stats
        per_side_stats: dict[int, SideStats] = compute_fight_sides(
            cluster_wrapped,  # type: ignore[arg-type]
            side_for_alliance,
        )

        # Label sides
        label_input = _build_label_input(per_side_stats, side_result)
        labels = label_sides(
            label_input,
            our_alliance_ids=our_alliance_set,
            our_corp_ids=our_corp_set,
        )

        # Fight-level time bounds
        times = [w.killmail_time for w in cluster_wrapped]
        started_at = min(times)
        ended_at = max(times)
        if earliest_start is None or started_at < earliest_start:
            earliest_start = started_at

        # Fight-level aggregates
        isk_destroyed_total = sum(sd.isk_lost for sd in per_side_stats.values())
        all_alliance_ids: set[int] = set()
        for sd in per_side_stats.values():
            all_alliance_ids |= sd.alliance_ids
        largest_side_pilots = max(
            (sd.pilot_count for sd in per_side_stats.values()),
            default=0,
        )

        # Persist Fight
        fight = Fight(
            system_id=cluster_wrapped[0].solar_system_id,
            started_at=started_at,
            ended_at=ended_at,
            isk_destroyed_total=isk_destroyed_total,
            largest_side_pilots=largest_side_pilots,
            # TODO(Task 4): detect capitals via SDE ship category and backfill
            capitals_involved=False,
            distinct_alliance_count=len(all_alliance_ids),
        )
        session.add(fight)
        await session.flush()  # get fight_id

        fight_id = fight.fight_id

        # Persist FightSide rows
        for side_idx, sd in per_side_stats.items():
            session.add(FightSide(
                fight_id=fight_id,
                side_idx=side_idx,
                pilot_count=sd.pilot_count,
                isk_lost=sd.isk_lost,
                alliance_ids_json=json.dumps(sorted(sd.alliance_ids)),
                corp_ids_json=json.dumps(sorted(sd.corp_ids)),
                side_kind=labels.get(side_idx),
            ))

        # Persist FightKill rows
        for km_id, victim_side in side_result.kill_victim_side.items():
            session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=victim_side))

        # Persist BrFight
        session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=fight_seq))
        fight_seq += 1

        # Persist fight_ship_count
        # Dedupe by (side_idx, character_id) so each pilot is counted once per ship.
        # Victim's lost ship is always counted (no character dedup needed for victims
        # since each kill has one victim). Attackers are deduped so a pilot appearing
        # in N kills contributes only 1 to their ship's count.
        fight_ship_accumulator: dict[tuple[int, int], int] = defaultdict(int)
        attacker_seen: set[tuple[int, int]] = set()  # (side_idx, character_id)
        for w in cluster_wrapped:
            v_aid = w.victim_alliance_id
            v_side = side_for_alliance.get(v_aid, 0) if v_aid is not None else 0
            fight_ship_accumulator[(v_side, w.victim_ship_type_id)] += 1
            for att in w.attackers:
                a_aid = att.alliance_id
                if a_aid is not None:
                    a_side = side_for_alliance.get(a_aid)
                    if a_side is not None and att.ship_type_id is not None:
                        if att.character_id is not None:
                            key = (a_side, att.character_id)
                            if key in attacker_seen:
                                continue
                            attacker_seen.add(key)
                        fight_ship_accumulator[(a_side, att.ship_type_id)] += 1

        for (sidx, ship_id), cnt in fight_ship_accumulator.items():
            session.add(FightShipCount(
                fight_id=fight_id, side_idx=sidx, ship_type_id=ship_id, count=cnt
            ))

        # Accumulate BR-level ISK by side_kind
        for side_idx, sd in per_side_stats.items():
            side_kind = labels.get(side_idx)
            if side_kind == "friendly":
                br_our_lost += sd.isk_lost
            elif side_kind == "hostile":
                br_our_destroyed += sd.isk_lost

            if side_kind is not None:
                for ship_id, cnt in sd.ship_counts.items():
                    br_ship_accumulator[(side_kind, ship_id)] += cnt

    await session.flush()

    # Persist br_ship_count
    for (side_kind, ship_id), cnt in br_ship_accumulator.items():
        session.add(BrShipCount(
            br_id=br_id, side_kind=side_kind, ship_type_id=ship_id, count=cnt
        ))

    # BR-level efficiency and result
    isk_eff: float | None = (
        br_our_destroyed / (br_our_destroyed + br_our_lost)
        if (br_our_destroyed + br_our_lost) > 0
        else None
    )
    br_result = classify_br_result(br_our_destroyed, br_our_lost)

    # Update BattleReport
    br_row_result = await session.execute(
        select(BattleReport).where(BattleReport.br_id == br_id)
    )
    br_row = br_row_result.scalar_one_or_none()
    if br_row is not None:
        br_row.our_isk_destroyed = br_our_destroyed
        br_row.our_isk_lost = br_our_lost
        br_row.isk_efficiency = isk_eff
        br_row.result = br_result
        br_row.battle_at = earliest_start
        br_row.fight_count = fight_seq

    await session.flush()
    log.info(
        "aggregate_br.done",
        br_id=br_id,
        fight_count=fight_seq,
        result=br_result,
        isk_efficiency=isk_eff,
    )
