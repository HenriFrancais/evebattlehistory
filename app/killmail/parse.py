"""Parse a raw killmail JSON dict into a typed ParsedKillmail."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel

from app.killmail.flags import flag_to_location


class ParsedAttacker(BaseModel):
    attacker_idx: int
    character_id: int | None = None
    corporation_id: int | None = None
    alliance_id: int | None = None
    ship_type_id: int | None = None
    weapon_type_id: int | None = None
    damage_done: int = 0
    final_blow: bool = False
    security_status: float | None = None


class ParsedItem(BaseModel):
    item_idx: int
    type_id: int
    flag: int
    location: str
    qty_destroyed: int = 0
    qty_dropped: int = 0
    singleton: bool = False


class ParsedVictim(BaseModel):
    character_id: int | None = None
    corporation_id: int | None = None
    alliance_id: int | None = None
    ship_type_id: int


class ParsedKillmail(BaseModel):
    killmail_id: int
    killmail_time: dt.datetime
    solar_system_id: int
    victim: ParsedVictim
    attackers: list[ParsedAttacker]
    items: list[ParsedItem]
    total_value: float | None = None
    fitted_value: float | None = None
    npc_kill: bool = False
    solo_kill: bool = False
    points: int | None = None
    hash: str | None = None


def _d(data: Any, key: str, default: Any = None) -> Any:
    """Safe dict access returning Any."""
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def parse_killmail(raw: dict[str, object]) -> ParsedKillmail:
    victim_raw: Any = raw.get("victim", {})
    items: list[ParsedItem] = []
    victim_items: Any = _d(victim_raw, "items", [])
    for idx, item in enumerate(victim_items if isinstance(victim_items, list) else []):
        item_d: Any = item
        flag: int = int(_d(item_d, "flag", 0))
        items.append(ParsedItem(
            item_idx=idx,
            type_id=int(_d(item_d, "item_type_id", 0)),
            flag=flag,
            location=flag_to_location(flag),
            qty_destroyed=int(_d(item_d, "quantity_destroyed", 0)),
            qty_dropped=int(_d(item_d, "quantity_dropped", 0)),
            singleton=bool(_d(item_d, "singleton", 0)),
        ))

    attackers: list[ParsedAttacker] = []
    raw_attackers: Any = raw.get("attackers", [])
    for idx, att in enumerate(raw_attackers if isinstance(raw_attackers, list) else []):
        att_d: Any = att
        char_id_raw = _d(att_d, "character_id")
        corp_id_raw = _d(att_d, "corporation_id")
        ally_id_raw = _d(att_d, "alliance_id")
        ship_id_raw = _d(att_d, "ship_type_id")
        weap_id_raw = _d(att_d, "weapon_type_id")
        sec_raw = _d(att_d, "security_status")
        attackers.append(ParsedAttacker(
            attacker_idx=idx,
            character_id=int(char_id_raw) if char_id_raw is not None else None,
            corporation_id=int(corp_id_raw) if corp_id_raw is not None else None,
            alliance_id=int(ally_id_raw) if ally_id_raw is not None else None,
            ship_type_id=int(ship_id_raw) if ship_id_raw is not None else None,
            weapon_type_id=int(weap_id_raw) if weap_id_raw is not None else None,
            damage_done=int(_d(att_d, "damage_done", 0)),
            final_blow=bool(_d(att_d, "final_blow", False)),
            security_status=float(sec_raw) if sec_raw is not None else None,
        ))

    zkb: Any = raw.get("zkb", {})
    total_v = _d(zkb, "totalValue")
    fitted_v = _d(zkb, "fittedValue")
    pts = _d(zkb, "points")
    km_hash = _d(zkb, "hash")

    v_char = _d(victim_raw, "character_id")
    v_corp = _d(victim_raw, "corporation_id")
    v_ally = _d(victim_raw, "alliance_id")

    return ParsedKillmail(
        killmail_id=int(str(raw["killmail_id"])),
        killmail_time=raw["killmail_time"],  # type: ignore[arg-type]
        solar_system_id=int(str(raw["solar_system_id"])),
        victim=ParsedVictim(
            character_id=int(v_char) if v_char is not None else None,
            corporation_id=int(v_corp) if v_corp is not None else None,
            alliance_id=int(v_ally) if v_ally is not None else None,
            ship_type_id=int(_d(victim_raw, "ship_type_id", 0)),
        ),
        attackers=attackers,
        items=items,
        total_value=float(total_v) if total_v is not None else None,
        fitted_value=float(fitted_v) if fitted_v is not None else None,
        npc_kill=bool(_d(zkb, "npc", False)),
        solo_kill=bool(_d(zkb, "solo", False)),
        points=int(pts) if pts is not None else None,
        hash=str(km_hash) if km_hash is not None else None,
    )
