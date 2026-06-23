"""Read-time off-BR participant identification + sides-editor surfacing."""
from __future__ import annotations

import datetime as dt

import pytest

from tests.test_e1_log_only_participants import (
    CHAR_ATTACKER,
    TS_INSIDE,
    _insert_fight_with_killmail,
    _insert_gamelog_file,
)

OFFBR_LOGI = 5100000001   # log-uploader, off-BR, has corp/alliance
HOSTILE_GUY = 5100000002  # counterparty only, off-BR
GUARDIAN = 11987
LOGI_ALLI, LOGI_CORP = 222, 111
HOSTILE_ALLI, HOSTILE_CORP = 444, 333


async def _seed_offbr_br(session):  # type: ignore[no-untyped-def]
    """Seed a BR/fight + an off-BR log-uploader and an off-BR counterparty (a
    Guardian named HostileGuy). Returns br_id."""
    from app.db.models import Alliance, Character, Corporation, InventoryType, LogEvent
    from app.logs.associate import associate_file

    now = dt.datetime.now(dt.UTC)
    _fight_id, br_id = await _insert_fight_with_killmail(session)
    session.add(InventoryType(type_id=GUARDIAN, name="Guardian", category_id=6))
    for aid in (LOGI_ALLI, HOSTILE_ALLI):
        session.add(Alliance(alliance_id=aid, name=f"Alli{aid}", last_seen_at=now))
    session.add(Corporation(corporation_id=LOGI_CORP, name="Corp111", alliance_id=LOGI_ALLI, last_seen_at=now))
    session.add(Corporation(corporation_id=HOSTILE_CORP, name="Corp333", alliance_id=HOSTILE_ALLI, last_seen_at=now))
    session.add(Character(character_id=OFFBR_LOGI, name="OffbrLogi", corporation_id=LOGI_CORP, alliance_id=LOGI_ALLI, last_seen_at=now))
    session.add(Character(character_id=HOSTILE_GUY, name="HostileGuy", corporation_id=HOSTILE_CORP, alliance_id=HOSTILE_ALLI, last_seen_at=now))
    await session.flush()

    fid = await _insert_gamelog_file(session, character_id=OFFBR_LOGI)
    session.add(LogEvent(file_id=fid, character_id=OFFBR_LOGI, ts=TS_INSIDE,
                         direction="out", effect_type="rep_armor", amount=500.0, other_name="ConfirmedChar"))
    session.add(LogEvent(file_id=fid, character_id=OFFBR_LOGI, ts=TS_INSIDE,
                         direction="out", effect_type="neut", amount=0.0,
                         other_name="HostileGuy", other_ship_name="Guardian"))
    await associate_file(session, fid)
    return br_id


@pytest.mark.asyncio
async def test_offbr_log_characters_identifies_owner_and_counterparty(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.fights.offbr_participants import offbr_log_characters

    settings = get_settings()
    async with db_session_maker() as session:
        br_id = await _seed_offbr_br(session)
        await session.commit()

    async with db_session_maker() as session:
        result = await offbr_log_characters(session, settings, br_id)

    by_id = {p.character_id: p for p in result}
    assert CHAR_ATTACKER not in by_id  # on-BR excluded
    assert by_id[OFFBR_LOGI].source == "log_owner"
    assert by_id[OFFBR_LOGI].alliance_id == LOGI_ALLI
    assert by_id[HOSTILE_GUY].source == "counterparty"
    assert by_id[HOSTILE_GUY].detected_ship_type_id == GUARDIAN


@pytest.mark.asyncio
async def test_br_entities_includes_offbr_entity_and_respects_override(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.sides_config import br_entities
    from app.config import get_settings
    from app.db.models import BrSideOverride

    settings = get_settings()
    async with db_session_maker() as session:
        br_id = await _seed_offbr_br(session)
        await session.commit()

    # The hostile counterparty's alliance is on no killmail — it must still appear.
    async with db_session_maker() as session:
        ents = await br_entities(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=settings,
        )
    hostile_ent = next((e for e in ents if e["entity_type"] == "alliance" and e["entity_id"] == HOSTILE_ALLI), None)
    assert hostile_ent is not None, "off-BR participant's alliance missing from sides editor"
    assert hostile_ent["side"] == "unassigned"

    # After an FC/HC override, it classifies hostile.
    async with db_session_maker() as session:
        session.add(BrSideOverride(br_id=br_id, entity_type="alliance", entity_id=HOSTILE_ALLI, side="hostile"))
        await session.commit()
    async with db_session_maker() as session:
        from app.analytics.sides_config import load_overrides
        overrides = await load_overrides(session, br_id)
        ents = await br_entities(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides=overrides, settings=settings,
        )
    hostile_ent = next(e for e in ents if e["entity_type"] == "alliance" and e["entity_id"] == HOSTILE_ALLI)
    assert hostile_ent["side"] == "hostile"
