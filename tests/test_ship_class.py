"""Pilot lists run from the largest hull class to the smallest."""

from __future__ import annotations

from app.analytics.composition import CompositionPilot
from app.analytics.ship_class import UNKNOWN_RANK, ship_size_rank


def test_larger_classes_rank_before_smaller_ones() -> None:
    order = ["Titan", "Dreadnought", "Battleship", "Command Ship", "Strategic Cruiser",
             "Logistics", "Destroyer", "Frigate", "Capsule"]
    ranks = [ship_size_rank(g) for g in order]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert ranks[0] == 0


def test_unknown_group_or_no_hull_sorts_last() -> None:
    assert ship_size_rank("Some Future Class") == UNKNOWN_RANK
    assert ship_size_rank(None) == UNKNOWN_RANK
    assert UNKNOWN_RANK > ship_size_rank("Capsule")


def test_composition_pilot_defaults_to_the_unknown_rank() -> None:
    p = CompositionPilot(
        character_id=1, character_name="A", ship_type_id=None, ship_name="Unknown", lost=False,
        reship=False, user_name=None, killmail_id=None, weapons=[],
    )
    assert (p.ship_group, p.ship_rank) == (None, UNKNOWN_RANK)
