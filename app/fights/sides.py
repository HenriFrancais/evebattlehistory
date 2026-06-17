"""Side detection: build the attack graph, 2-color it via BFS.

Adapted from fitstory/src/fitstory/fights/sides.py.  Works on any killmail-like
objects that expose victim_alliance_id, victim_corporation_id, and an attackers
iterable (each with alliance_id, corporation_id, ship_type_id).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol


class _AttackerProto(Protocol):
    alliance_id: int | None
    corporation_id: int | None
    ship_type_id: int | None


class _KillProto(Protocol):
    killmail_id: int
    victim_alliance_id: int | None
    victim_corporation_id: int | None
    victim_ship_type_id: int
    total_value: float | None
    attackers: list[_AttackerProto]


@dataclass
class SideResult:
    """Result of side-assignment for a fight cluster."""

    # alliance_id → side_idx (0 or 1)
    alliance_sides: dict[int, int] = field(default_factory=dict)
    # corp_id → side_idx (0 or 1); for entities without alliance
    corp_sides: dict[int, int] = field(default_factory=dict)
    # side_idx → set of alliance_ids
    side_alliances: dict[int, set[int]] = field(default_factory=dict)
    # side_idx → set of corp_ids
    side_corps: dict[int, set[int]] = field(default_factory=dict)
    # side_idx → set of pilot ship_type_ids (for capital detection etc.)
    side_ships: dict[int, set[int]] = field(default_factory=dict)
    # side_idx → set of killmail_ids whose victim is on that side
    victim_sides: dict[int, set[int]] = field(default_factory=dict)
    # killmail_id → side_idx of the victim
    kill_victim_side: dict[int, int] = field(default_factory=dict)


def assign_sides(kills: list[_KillProto]) -> SideResult:
    """2-color the fight graph; return per-side membership.

    Algorithm:
      1. Collect all alliance IDs that appear as victim or attacker.
      2. Build undirected enemy graph: edge when an alliance attacked another.
         Co-attackers (same kill, different alliances) are on the SAME side.
      3. 2-color each connected component via BFS.
      4. Assign corp_sides for corps whose alliance is None.

    NPC-only attacks (alliance_id=None on every attacker) are handled
    gracefully: the victim alliance lands on side 0 with no opposition.
    """
    all_alliances: set[int] = set()
    all_corps: set[int] = set()
    attack_pairs: set[tuple[int, int]] = set()  # (attacker_alliance, victim_alliance)

    # Same-side co-attacker pairs (same kill, different alliances)
    co_attacker_pairs: set[tuple[int, int]] = set()

    for k in kills:
        v_aid = k.victim_alliance_id
        if v_aid is not None:
            all_alliances.add(v_aid)

        attacker_aids: list[int] = []
        for a in k.attackers:
            a_aid = a.alliance_id
            if a_aid is not None:
                all_alliances.add(a_aid)
                attacker_aids.append(a_aid)
                if v_aid is not None and a_aid != v_aid:
                    attack_pairs.add((a_aid, v_aid))
            elif a.corporation_id is not None:
                all_corps.add(a.corporation_id)

        # Co-attackers on the same kill → same side
        for i in range(len(attacker_aids)):
            for j in range(i + 1, len(attacker_aids)):
                a1, a2 = sorted((attacker_aids[i], attacker_aids[j]))
                if a1 != a2:
                    co_attacker_pairs.add((a1, a2))

    # Build adjacency: enemy edges (different sides) — bidirectional
    adj: dict[int, set[int]] = {a: set() for a in all_alliances}
    for atk, vic in attack_pairs:
        adj[atk].add(vic)
        adj[vic].add(atk)

    # 2-colour via BFS; co-attacker edges force same colour
    color: dict[int, int] = {}
    for start in sorted(all_alliances):
        if start in color:
            continue
        color[start] = 0
        queue: deque[int] = deque([start])
        while queue:
            u = queue.popleft()
            for v in adj[u]:
                if v not in color:
                    color[v] = 1 - color[u]
                    queue.append(v)

    # Enforce co-attacker same-side (in case BFS assigned different colours)
    for a1, a2 in co_attacker_pairs:
        if a1 in color and a2 in color and color[a1] != color[a2]:
            # Re-assign a2's component to a1's colour
            target = color[a1]
            old_colour = color[a2]
            for k_id, c in color.items():
                if c == old_colour:
                    color[k_id] = target

    # Build result
    result = SideResult()
    result.alliance_sides = dict(color)
    result.side_alliances = {}
    result.side_corps = {}
    result.side_ships = {}
    result.victim_sides = {}
    result.kill_victim_side = {}

    for aid, side in color.items():
        result.side_alliances.setdefault(side, set()).add(aid)

    # Handle corpsN with no alliance (greedy: first kill that shows this corp attacking)
    # They don't participate in 2-colouring but we note them
    for corp_id in all_corps:
        result.corp_sides[corp_id] = 0  # default side

    # Assign kill_victim_side based on victim's alliance
    for k in kills:
        v_aid = k.victim_alliance_id
        if v_aid is not None and v_aid in color:
            side = color[v_aid]
        else:
            side = 0
        result.kill_victim_side[k.killmail_id] = side
        result.victim_sides.setdefault(side, set()).add(k.killmail_id)

    return result
