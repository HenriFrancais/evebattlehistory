"""Same-system windowed transitive-closure fight clustering.

Adapted from fitstory/src/fitstory/fights/cluster.py with a tighter default
window (12 min vs 45) and parameterised thresholds so distinct skirmishes
inside one BR can be separated cleanly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol


class _KillProto(Protocol):
    killmail_id: int
    solar_system_id: int
    killmail_time: dt.datetime


@dataclass
class FightCluster:
    killmail_ids: list[int] = field(default_factory=list)
    kills: list[_KillProto] = field(default_factory=list)


def _close_chain(
    sorted_kills: list[_KillProto], window: dt.timedelta
) -> list[list[_KillProto]]:
    """Single-pass transitive closure: split a sorted list into runs where each
    kill is within ``window`` of the previous one in its run."""
    if not sorted_kills:
        return []
    runs: list[list[_KillProto]] = [[sorted_kills[0]]]
    for k in sorted_kills[1:]:
        if k.killmail_time - runs[-1][-1].killmail_time <= window:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


def _split_on_max_duration(
    run: list[_KillProto], max_duration: dt.timedelta
) -> list[list[_KillProto]]:
    """Split a run at the largest internal gap until every piece spans <= max_duration.

    Ties among equally-large gaps are broken by proximity to the midpoint, so
    uniformly-spaced runs split into balanced halves rather than degenerating
    into single-kill suffixes.
    """
    pieces: list[list[_KillProto]] = [run]
    out: list[list[_KillProto]] = []
    while pieces:
        piece = pieces.pop()
        span = piece[-1].killmail_time - piece[0].killmail_time
        if span <= max_duration or len(piece) < 2:
            out.append(piece)
            continue
        n_gaps = len(piece) - 1
        mid = (n_gaps - 1) / 2
        gaps = [
            (piece[i + 1].killmail_time - piece[i].killmail_time, -abs(i - mid), i)
            for i in range(n_gaps)
        ]
        _, _, split_idx = max(gaps)
        left, right = piece[: split_idx + 1], piece[split_idx + 1 :]
        pieces.extend([left, right])
    out.sort(key=lambda r: r[0].killmail_time)
    return out


def cluster_kills(
    kills: list[_KillProto],
    *,
    window_minutes: int = 12,
    max_duration_minutes: int = 180,
) -> list[FightCluster]:
    """Cluster kills into fights.

    1. Group by solar_system_id.
    2. Within a system, sort by killmail_time.
    3. Transitive closure within ``window_minutes``.
    4. Cap each chain at ``max_duration_minutes`` (split at largest internal gap).
    """
    window = dt.timedelta(minutes=window_minutes)
    max_dur = dt.timedelta(minutes=max_duration_minutes)

    by_system: dict[int, list[_KillProto]] = {}
    for k in kills:
        by_system.setdefault(k.solar_system_id, []).append(k)

    fights: list[FightCluster] = []
    for _system_id, sys_kills in by_system.items():
        sys_kills.sort(key=lambda k: k.killmail_time)
        for run in _close_chain(sys_kills, window):
            for piece in _split_on_max_duration(run, max_dur):
                fights.append(
                    FightCluster(
                        killmail_ids=[k.killmail_id for k in piece],
                        kills=list(piece),
                    )
                )
    fights.sort(key=lambda f: f.kills[0].killmail_time)
    return fights
