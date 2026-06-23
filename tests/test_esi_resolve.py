"""Tests for ESI name->id and affiliation resolution (real client + demo stub)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_demo_resolve_ids_and_affiliations(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "ids.json").write_text('{"Bob Pilot": 100, "Cap Sula": 101}')
    (tmp_path / "affiliations.json").write_text(
        '{"100": {"corporation_id": 5, "alliance_id": 9}}'
    )
    from app.esi.demo import DemoEsiClient

    c = DemoEsiClient(tmp_path)
    assert await c.resolve_ids(["Bob Pilot", "Nope"]) == {"Bob Pilot": 100}
    assert await c.resolve_affiliations([100, 101]) == {100: (5, 9), 101: (None, None)}


@pytest.mark.asyncio
async def test_demo_resolve_missing_files_return_empty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.esi.demo import DemoEsiClient

    c = DemoEsiClient(tmp_path)
    assert await c.resolve_ids(["Anyone"]) == {}
    assert await c.resolve_affiliations([1]) == {1: (None, None)}
