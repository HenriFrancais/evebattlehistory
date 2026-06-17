"""Label fight sides as friendly / hostile / neutral relative to configured "us" entities.

Pure function — no DB access, no config import. Caller passes the entity sets.

A side is:
  friendly  if its alliance_ids or corp_ids intersect the "us" sets
  hostile   if it directly opposes a friendly side (i.e. there exists an
            enemy-graph edge between this side and a friendly side)
  neutral   if no friendly side was found, OR this side did not directly
            oppose a friendly side
"""

from __future__ import annotations

SideInfo = dict[str, set[int]]  # {"alliance_ids": ..., "corp_ids": ...}


def label_sides(
    per_side: dict[int, SideInfo],
    *,
    our_alliance_ids: set[int],
    our_corp_ids: set[int],
    opposing_sides: set[tuple[int, int]] | None = None,
) -> dict[int, str]:
    """Label each side as 'friendly', 'hostile', or 'neutral'.

    Args:
        per_side: mapping of side_idx → {"alliance_ids": set, "corp_ids": set}
        our_alliance_ids: alliance IDs that belong to "us"
        our_corp_ids: corp IDs that belong to "us"
        opposing_sides: optional set of (side_a, side_b) pairs (unordered) indicating
            which sides directly fought each other. When provided, a non-friendly side
            is hostile only if it has an opposing edge to a friendly side. When None,
            all non-friendly sides are hostile if any friendly side exists (2-sided fight
            assumption).

    Returns:
        mapping of side_idx → "friendly" | "hostile" | "neutral"
    """
    friendly_sides: set[int] = set()
    for idx, info in per_side.items():
        if info["alliance_ids"] & our_alliance_ids or info["corp_ids"] & our_corp_ids:
            friendly_sides.add(idx)

    labels: dict[int, str] = {}
    for idx in per_side:
        if idx in friendly_sides:
            labels[idx] = "friendly"
            continue

        if not friendly_sides:
            labels[idx] = "neutral"
            continue

        if opposing_sides is not None:
            # Hostile only if directly opposing a friendly side
            is_hostile = any(
                (idx, f) in opposing_sides or (f, idx) in opposing_sides
                for f in friendly_sides
            )
            labels[idx] = "hostile" if is_hostile else "neutral"
        else:
            # No graph info: assume 2-sided fight, all non-friendly = hostile
            labels[idx] = "hostile"

    return labels
