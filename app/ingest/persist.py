"""Persist parsed killmails into the database using SQLite upserts."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alliance,
    Character,
    Corporation,
    InventoryType,
    Killmail,
    KillmailAttacker,
    KillmailItem,
    SolarSystem,
)
from app.killmail.parse import ParsedKillmail, parse_killmail
from app.observability.logging import log


def _find_alliance_for_corp(parsed: list[ParsedKillmail], corp_id: int) -> int | None:
    """Return the first alliance_id seen for *corp_id* across all killmails."""
    for km in parsed:
        if km.victim.corporation_id == corp_id and km.victim.alliance_id:
            return km.victim.alliance_id
        for att in km.attackers:
            if att.corporation_id == corp_id and att.alliance_id:
                return att.alliance_id
    return None


def _find_corp_for_character(
    parsed: list[ParsedKillmail], char_id: int
) -> tuple[int | None, int | None]:
    """Return the first (corporation_id, alliance_id) seen for *char_id*."""
    for km in parsed:
        if km.victim.character_id == char_id:
            return km.victim.corporation_id, km.victim.alliance_id
        for att in km.attackers:
            if att.character_id == char_id:
                return att.corporation_id, att.alliance_id
    return None, None


async def persist_killmails(
    session: AsyncSession,
    killmails_json: list[dict[str, object]],
    names: dict[int, dict[str, str]],
    values: dict[int, float | None] | None = None,
) -> int:
    """Parse and persist killmails. Returns count of newly inserted killmails."""
    if not killmails_json:
        return 0
    values = values or {}

    now = dt.datetime.now(dt.UTC)

    # Parse all killmails
    parsed: list[ParsedKillmail] = []
    for raw in killmails_json:
        kid = raw.get("killmail_id")
        try:
            tv = values.get(int(str(kid))) if kid is not None else None
        except (TypeError, ValueError):
            tv = None
        if tv is not None:
            zkb = raw.get("zkb")
            if not isinstance(zkb, dict):
                zkb = {}
                raw["zkb"] = zkb
            zkb.setdefault("totalValue", tv)
        try:
            parsed.append(parse_killmail(raw))
        except Exception as exc:
            log.warning("persist.parse_failed", error=str(exc))

    if not parsed:
        return 0

    # Collect unique IDs
    solar_system_ids: set[int] = set()
    type_ids: set[int] = set()
    alliance_ids: set[int] = set()
    corporation_ids: set[int] = set()
    character_ids: set[int] = set()

    for km in parsed:
        solar_system_ids.add(km.solar_system_id)
        type_ids.add(km.victim.ship_type_id)
        if km.victim.alliance_id:
            alliance_ids.add(km.victim.alliance_id)
        if km.victim.corporation_id:
            corporation_ids.add(km.victim.corporation_id)
        if km.victim.character_id:
            character_ids.add(km.victim.character_id)
        for att in km.attackers:
            if att.alliance_id:
                alliance_ids.add(att.alliance_id)
            if att.corporation_id:
                corporation_ids.add(att.corporation_id)
            if att.character_id:
                character_ids.add(att.character_id)
            if att.ship_type_id:
                type_ids.add(att.ship_type_id)
            if att.weapon_type_id:
                type_ids.add(att.weapon_type_id)
        for item in km.items:
            type_ids.add(item.type_id)

    # Upsert SolarSystem rows
    if solar_system_ids:
        ss_rows: list[dict[str, object]] = []
        for sid in solar_system_ids:
            info = names.get(sid, {})
            ss_rows.append({
                "system_id": sid,
                "name": info.get("name", str(sid)),
                "security": None,
            })
        ss_stmt = sqlite_insert(SolarSystem).values(ss_rows)
        ss_stmt = ss_stmt.on_conflict_do_nothing(index_elements=["system_id"])
        await session.execute(ss_stmt)

    # Upsert InventoryType rows
    if type_ids:
        it_rows: list[dict[str, object]] = []
        for tid in type_ids:
            info = names.get(tid, {})
            it_rows.append({
                "type_id": tid,
                "name": info.get("name", str(tid)),
                "group_id": 0,
                "group_name": "Unknown",
                "category_id": 0,
                "category_name": "Unknown",
                "market_group_id": None,
            })
        it_stmt = sqlite_insert(InventoryType).values(it_rows)
        it_stmt = it_stmt.on_conflict_do_nothing(index_elements=["type_id"])
        await session.execute(it_stmt)

    # Upsert Alliance rows (update name on conflict)
    if alliance_ids:
        al_rows: list[dict[str, object]] = []
        for aid in alliance_ids:
            info = names.get(aid, {})
            al_rows.append({
                "alliance_id": aid,
                "name": info.get("name"),
                "last_seen_at": now,
            })
        al_stmt = sqlite_insert(Alliance).values(al_rows)
        al_stmt = al_stmt.on_conflict_do_update(
            index_elements=["alliance_id"],
            set_={"name": al_stmt.excluded.name, "last_seen_at": al_stmt.excluded.last_seen_at},
        )
        await session.execute(al_stmt)

    # Upsert Corporation rows
    if corporation_ids:
        co_rows: list[dict[str, object]] = []
        for cid in corporation_ids:
            info = names.get(cid, {})
            corp_alliance = _find_alliance_for_corp(parsed, cid)
            co_rows.append({
                "corporation_id": cid,
                "name": info.get("name"),
                "alliance_id": corp_alliance,
                "last_seen_at": now,
            })
        co_stmt = sqlite_insert(Corporation).values(co_rows)
        co_stmt = co_stmt.on_conflict_do_update(
            index_elements=["corporation_id"],
            set_={
                "name": co_stmt.excluded.name,
                "alliance_id": co_stmt.excluded.alliance_id,
                "last_seen_at": co_stmt.excluded.last_seen_at,
            },
        )
        await session.execute(co_stmt)

    # Upsert Character rows
    if character_ids:
        ch_rows: list[dict[str, object]] = []
        for chid in character_ids:
            info = names.get(chid, {})
            char_corp, char_alliance = _find_corp_for_character(parsed, chid)
            ch_rows.append({
                "character_id": chid,
                "name": info.get("name"),
                "corporation_id": char_corp,
                "alliance_id": char_alliance,
                "last_seen_at": now,
            })
        ch_stmt = sqlite_insert(Character).values(ch_rows)
        ch_stmt = ch_stmt.on_conflict_do_update(
            index_elements=["character_id"],
            set_={
                "name": ch_stmt.excluded.name,
                "corporation_id": ch_stmt.excluded.corporation_id,
                "alliance_id": ch_stmt.excluded.alliance_id,
                "last_seen_at": ch_stmt.excluded.last_seen_at,
            },
        )
        await session.execute(ch_stmt)

    # Check existing killmail_ids
    incoming_ids = [km.killmail_id for km in parsed]
    result = await session.execute(
        select(Killmail.killmail_id).where(Killmail.killmail_id.in_(incoming_ids))
    )
    existing_ids = set(result.scalars())

    new_kms = [km for km in parsed if km.killmail_id not in existing_ids]

    # Backfill ISK value on refresh: existing rows may have been inserted before a
    # value was known. For parsed killmails already present whose total_value is now
    # known, fill it WITHOUT overwriting an existing non-null value.
    for km in parsed:
        if km.killmail_id in existing_ids and km.total_value is not None:
            await session.execute(
                update(Killmail)
                .where(
                    Killmail.killmail_id == km.killmail_id,
                    Killmail.total_value.is_(None),
                )
                .values(total_value=km.total_value)
            )

    if not new_kms:
        return 0

    # Insert new killmails
    km_rows: list[dict[str, object]] = []
    for km in new_kms:
        km_rows.append({
            "killmail_id": km.killmail_id,
            "killmail_time": km.killmail_time,
            "solar_system_id": km.solar_system_id,
            "victim_character_id": km.victim.character_id,
            "victim_corporation_id": km.victim.corporation_id,
            "victim_alliance_id": km.victim.alliance_id,
            "victim_ship_type_id": km.victim.ship_type_id,
            "total_value": km.total_value,
            "fitted_value": km.fitted_value,
            "npc_kill": km.npc_kill,
            "solo_kill": km.solo_kill,
            "points": km.points,
            "hash": km.hash,
            "damage_taken": km.victim.damage_taken,
        })
    await session.execute(sqlite_insert(Killmail).values(km_rows))

    # Insert attackers
    attacker_rows: list[dict[str, object]] = []
    for km in new_kms:
        for att in km.attackers:
            attacker_rows.append({
                "killmail_id": km.killmail_id,
                "attacker_idx": att.attacker_idx,
                "character_id": att.character_id,
                "corporation_id": att.corporation_id,
                "alliance_id": att.alliance_id,
                "ship_type_id": att.ship_type_id,
                "weapon_type_id": att.weapon_type_id,
                "damage_done": att.damage_done,
                "final_blow": att.final_blow,
                "security_status": att.security_status,
            })
    if attacker_rows:
        await session.execute(sqlite_insert(KillmailAttacker).values(attacker_rows))

    # Insert items
    item_rows: list[dict[str, object]] = []
    for km in new_kms:
        for item in km.items:
            item_rows.append({
                "killmail_id": km.killmail_id,
                "item_idx": item.item_idx,
                "type_id": item.type_id,
                "flag": item.flag,
                "location": item.location,
                "qty_destroyed": item.qty_destroyed,
                "qty_dropped": item.qty_dropped,
                "singleton": item.singleton,
            })
    if item_rows:
        await session.execute(sqlite_insert(KillmailItem).values(item_rows))

    log.info("persist.killmails_inserted", count=len(new_kms))
    return len(new_kms)
