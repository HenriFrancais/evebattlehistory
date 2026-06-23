"""Read-time off-BR participant identification."""
from __future__ import annotations

import datetime as dt

import pytest

from tests.test_e1_log_only_participants import (
    CHAR_ATTACKER,
    TS_INSIDE,
    _insert_character,
    _insert_fight_with_killmail,
    _insert_gamelog_file,
)

OFFBR_LOGI = 5100000001   # log-uploader, off-BR, has corp/alliance
HOSTILE_GUY = 5100000002  # counterparty only, off-BR
GUARDIAN = 11987


@pytest.mark.asyncio
async def test_offbr_log_characters_identifies_owner_and_counterparty(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.models import Alliance, Character, Corporation, InventoryType, LogEvent
    from app.fights.offbr_participants import offbr_log_characters
    from app.logs.associate import associate_file

    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    async with db_session_maker() as session:
        _fight_id, br_id = await _insert_fight_with_killmail(session)
        session.add(InventoryType(type_id=GUARDIAN, name="Guardian", category_id=6))
        for aid in (222, 444):
            session.add(Alliance(alliance_id=aid, name=f"Alli{aid}", last_seen_at=now))
        session.add(Corporation(corporation_id=111, name="Corp111", alliance_id=222, last_seen_at=now))
        session.add(Corporation(corporation_id=333, name="Corp333", alliance_id=444, last_seen_at=now))
        await session.flush()
        # Off-BR log-uploader with a known corp/alliance.
        session.add(
            Character(
                character_id=OFFBR_LOGI, name="OffbrLogi",
                corporation_id=111, alliance_id=222, last_seen_at=dt.datetime.now(dt.UTC),
            )
        )
        # Counterparty-only character (resolves by name), flies a Guardian.
        session.add(
            Character(
                character_id=HOSTILE_GUY, name="HostileGuy",
                corporation_id=333, alliance_id=444, last_seen_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.flush()

        fid = await _insert_gamelog_file(session, character_id=OFFBR_LOGI)
        # A rep event (stamps the file to the fight) + an event naming HostileGuy in a Guardian.
        session.add(LogEvent(
            file_id=fid, character_id=OFFBR_LOGI, ts=TS_INSIDE,
            direction="out", effect_type="rep_armor", amount=500.0, other_name="ConfirmedChar",
        ))
        session.add(LogEvent(
            file_id=fid, character_id=OFFBR_LOGI, ts=TS_INSIDE,
            direction="out", effect_type="neut", amount=0.0,
            other_name="HostileGuy", other_ship_name="Guardian",
        ))
        await associate_file(session, fid)
        await session.commit()

    async with db_session_maker() as session:
        result = await offbr_log_characters(session, settings, br_id)

    by_id = {p.character_id: p for p in result}
    assert CHAR_ATTACKER not in by_id  # on-BR excluded
    assert OFFBR_LOGI in by_id
    assert by_id[OFFBR_LOGI].source == "log_owner"
    assert by_id[OFFBR_LOGI].alliance_id == 222
    assert HOSTILE_GUY in by_id
    assert by_id[HOSTILE_GUY].source == "counterparty"
    assert by_id[HOSTILE_GUY].detected_ship_type_id == GUARDIAN
