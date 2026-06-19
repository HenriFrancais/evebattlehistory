"""Unit tests for per-BR side classification (baseline + overrides)."""
from __future__ import annotations

from app.analytics.sides_config import classify_entity

BASELINE_A = {99006113, 99009324, 99014963}  # NV blues
BASELINE_C: set[int] = set()


def _c(alli, corp, overrides=None):
    return classify_entity(
        alli, corp, baseline_alliances=BASELINE_A, baseline_corps=BASELINE_C,
        overrides=overrides or {},
    )


def test_baseline_blue_is_friendly() -> None:
    assert _c(99006113, 98323701) == "friendly"


def test_unknown_alliance_is_unassigned_by_default() -> None:
    assert _c(99010787, 98422578) == "unassigned"


def test_override_makes_entity_friendly() -> None:
    # A blue that helped this BR — overridden friendly.
    assert _c(99013330, 1, overrides={("alliance", 99013330): "friendly"}) == "friendly"


def test_override_can_force_hostile_over_baseline() -> None:
    # Even a baseline blue can be forced hostile for one BR (blue-on-blue).
    assert _c(99006113, 1, overrides={("alliance", 99006113): "hostile"}) == "hostile"


def test_corp_override_when_no_alliance() -> None:
    assert _c(None, 98681825, overrides={("corp", 98681825): "friendly"}) == "friendly"
    assert _c(None, 98681825) == "unassigned"


def test_alliance_override_precedes_corp_override() -> None:
    ov = {("alliance", 100): "friendly", ("corp", 200): "hostile"}
    assert _c(100, 200, overrides=ov) == "friendly"
