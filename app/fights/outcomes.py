"""ISK-based outcome computation and BR-level win/tie/loss classification.

Win metric for the BR: ISK efficiency = our_destroyed / (our_destroyed + our_lost)
  win  if efficiency >= 0.52
  tie  if 0.48 <= efficiency < 0.52
  loss if efficiency < 0.48
  None if denominator is 0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

WIN_THRESHOLD = 0.52
TIE_THRESHOLD = 0.48  # lower boundary of tie band


class _AttackerProto(Protocol):
    character_id: int | None
    alliance_id: int | None
    corporation_id: int | None
    ship_type_id: int | None


class _KillProto(Protocol):
    killmail_id: int
    victim_character_id: int | None
    victim_alliance_id: int | None
    victim_corporation_id: int | None
    victim_ship_type_id: int
    total_value: float | None
    attackers: list[_AttackerProto]


@dataclass
class SideStats:
    """Per-side aggregate stats for a fight."""

    isk_lost: float = 0.0
    pilot_count: int = 0
    alliance_ids: set[int] = field(default_factory=set)
    corp_ids: set[int] = field(default_factory=set)
    character_ids: set[int] = field(default_factory=set)
    ship_counts: dict[int, int] = field(default_factory=dict)


def compute_outcome(
    sides_isk: dict[int, float],
) -> dict[int, dict[str, float | None]]:
    """Per-side ISK summary.

    Args:
        sides_isk: ``{side_idx: isk_lost}``

    Returns:
        ``{side_idx: {"isk_lost": float, "efficiency": float | None}}``

    ``efficiency`` here is the fraction of total ISK *not* lost by this side
    (i.e. how much the other sides lost relative to the total). It is ``None``
    when total ISK is 0.
    """
    total = sum(sides_isk.values())
    result: dict[int, dict[str, float | None]] = {}
    for idx, isk_lost in sides_isk.items():
        if total == 0.0:
            eff: float | None = None
        else:
            # efficiency = ISK they destroyed / total ISK destroyed
            # = (total - our_lost) / total
            eff = (total - isk_lost) / total
        result[idx] = {"isk_lost": isk_lost, "efficiency": eff}
    return result


def compute_fight_sides(
    kills: list[_KillProto],
    side_for_alliance: dict[int, int],
) -> dict[int, SideStats]:
    """Aggregate per-side stats across all kills in a fight.

    Returns mapping of ``side_idx`` → :class:`SideStats`.
    """
    per_side: dict[int, SideStats] = {}

    def _get(side: int) -> SideStats:
        if side not in per_side:
            per_side[side] = SideStats()
        return per_side[side]

    # Track (side_idx, character_id) pairs seen as attacker to deduplicate ship counts.
    # A pilot who appears as attacker across N kills contributes 1 to their ship count.
    attacker_seen: set[tuple[int, int]] = set()

    for k in kills:
        v_aid = k.victim_alliance_id
        v_cid = k.victim_corporation_id
        v_side = side_for_alliance.get(v_aid, 0) if v_aid is not None else 0
        sd = _get(v_side)

        sd.isk_lost += k.total_value or 0.0

        if v_aid is not None:
            sd.alliance_ids.add(v_aid)
        if v_cid is not None:
            sd.corp_ids.add(v_cid)
        if k.victim_character_id is not None:
            sd.character_ids.add(k.victim_character_id)

        # Count victim ship (each kill has exactly one victim, no dedup needed)
        sd.ship_counts[k.victim_ship_type_id] = (
            sd.ship_counts.get(k.victim_ship_type_id, 0) + 1
        )

        # Attackers
        for att in k.attackers:
            a_aid = att.alliance_id
            a_cid = att.corporation_id
            a_side = side_for_alliance.get(a_aid) if a_aid is not None else None
            if a_side is None:
                # No alliance means we cannot side-assign this attacker; skip.
                # Corp-only attackers are captured as victims when they lose ships.
                continue
            asd = _get(a_side)
            asd.alliance_ids.add(a_aid)  # type: ignore[arg-type]  # a_aid not None when a_side not None
            if a_cid is not None:
                asd.corp_ids.add(a_cid)
            if att.character_id is not None:
                asd.character_ids.add(att.character_id)
                # Deduplicate ship counts: count each pilot's ship once per fight
                if att.ship_type_id is not None:
                    char_key = (a_side, att.character_id)
                    if char_key not in attacker_seen:
                        attacker_seen.add(char_key)
                        asd.ship_counts[att.ship_type_id] = (
                            asd.ship_counts.get(att.ship_type_id, 0) + 1
                        )
            elif att.ship_type_id is not None:
                # No character_id (e.g. NPC in disguise, structure): count normally
                asd.ship_counts[att.ship_type_id] = (
                    asd.ship_counts.get(att.ship_type_id, 0) + 1
                )

    # Compute pilot_count from distinct character_ids per side
    for side_stats in per_side.values():
        side_stats.pilot_count = len(side_stats.character_ids)

    return per_side


def classify_br_result(our_destroyed: float, our_lost: float) -> str | None:
    """Classify a BR as win/tie/loss based on ISK efficiency.

    ISK efficiency = our_destroyed / (our_destroyed + our_lost)
    win  if efficiency >= 0.52
    tie  if 0.48 <= efficiency < 0.52
    loss if efficiency < 0.48
    None if denominator is 0
    """
    denom = our_destroyed + our_lost
    if denom == 0.0:
        return None
    eff = our_destroyed / denom
    if eff >= WIN_THRESHOLD:
        return "win"
    if eff >= TIE_THRESHOLD:
        return "tie"
    return "loss"
