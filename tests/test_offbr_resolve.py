"""Write-time off-BR counterparty resolution via (demo) ESI."""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_resolve_log_characters_persists_new_via_esi(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "ids.json").write_text('{"Hostile One": 700}')
    (tmp_path / "affiliations.json").write_text(
        '{"700": {"corporation_id": 70, "alliance_id": 77}}'
    )
    (tmp_path / "names.json").write_text(
        '{"70": {"name": "Hostile Corp", "category": "corporation"},'
        ' "77": {"name": "Hostile Alli", "category": "alliance"}}'
    )
    from app.config import get_settings
    from app.db.models import Alliance, Character, Corporation
    from app.esi.demo import DemoEsiClient
    from app.fights.offbr_resolve import resolve_log_characters

    async with db_session_maker() as session:
        session.add(
            Character(character_id=5, name="Already Known", last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.commit()

    settings = get_settings()
    esi = DemoEsiClient(tmp_path)
    async with db_session_maker() as session:
        n = await resolve_log_characters(
            session, settings,
            {"Hostile One", "Already Known", "   ", "---"},
            esi=esi,
        )
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        ch = (
            await session.execute(select(Character).where(Character.character_id == 700))
        ).scalar_one()
        assert ch.name == "Hostile One"
        assert ch.corporation_id == 70 and ch.alliance_id == 77
        corp = (
            await session.execute(select(Corporation).where(Corporation.corporation_id == 70))
        ).scalar_one()
        assert corp.name == "Hostile Corp" and corp.alliance_id == 77
        alli = (
            await session.execute(select(Alliance).where(Alliance.alliance_id == 77))
        ).scalar_one()
        assert alli.name == "Hostile Alli"


@pytest.mark.asyncio
async def test_resolve_log_characters_esi_failure_is_safe(db_session_maker, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.fights.offbr_resolve import resolve_log_characters

    class _BoomEsi:
        async def resolve_ids(self, names):  # type: ignore[no-untyped-def]
            raise RuntimeError("ESI down")

    async with db_session_maker() as session:
        n = await resolve_log_characters(
            session, get_settings(), {"Whoever"}, esi=_BoomEsi()
        )
    assert n == 0
