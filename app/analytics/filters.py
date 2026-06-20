"""Whitelisted predicate-tree compiler for fight and BR filters.

Security: unknown field or op MUST raise FilterError, never reach the DB.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Select

from app.db.models import (
    Alliance,
    BattleReport,
    BrFight,
    BrShipCount,
    Corporation,
    Fight,
    FightKill,
    FightShipCount,
    FightSide,
    InventoryType,
    Killmail,
    KillmailAttacker,
)


class FilterError(ValueError):
    pass


_NUMERIC_OPS = {">=", "<=", ">", "<", "==", "!="}
_DATETIME_OPS = {">=", "<=", "between"}
_ENUM_OPS = {"==", "in"}

_FIGHT_FIELDS: dict[str, Any] = {
    "isk_destroyed_total": Fight.isk_destroyed_total,
    "largest_side_pilots": Fight.largest_side_pilots,
    "distinct_alliance_count": Fight.distinct_alliance_count,
    "capitals_involved": Fight.capitals_involved,
    "system_id": Fight.system_id,
    "started_at": Fight.started_at,
}

_BR_FIELDS: dict[str, Any] = {
    "our_isk_destroyed": BattleReport.our_isk_destroyed,
    "our_isk_lost": BattleReport.our_isk_lost,
    "isk_efficiency": BattleReport.isk_efficiency,
    "result": BattleReport.result,
    "fight_count": BattleReport.fight_count,
    "battle_at": BattleReport.battle_at,
    "source": BattleReport.source,
}


def _apply_num_op(col: Any, op: str, value: Any) -> Any:
    if op == ">=":
        return col >= value
    if op == "<=":
        return col <= value
    if op == ">":
        return col > value
    if op == "<":
        return col < value
    if op == "==":
        return col == value
    if op == "!=":
        return col != value
    raise FilterError(f"Unknown numeric op: {op!r}")


def _entity_involved_clause(node: dict[str, Any], *, scope: str) -> Any:
    """Match BRs/fights where a corp or alliance name contains *name* (substring).

    Searches both victims and attackers across the unit's killmails, case-insensitively.
    ``scope`` is 'br' (correlate to BattleReport) or 'fight' (correlate to Fight).
    """
    name = node.get("name")
    if not isinstance(name, str) or not name.strip():
        raise FilterError("entity_involved leaf requires non-empty 'name' (str)")
    pattern = f"%{name.strip().lower()}%"

    def _exists(km_model: Any, join_cond: Any, name_model: Any) -> Any:
        stmt = (
            select(1)
            .select_from(km_model)
            .join(FightKill, FightKill.killmail_id == km_model.killmail_id)
            .join(name_model, join_cond)
            .where(func.lower(name_model.name).like(pattern))
        )
        if scope == "br":
            stmt = stmt.join(BrFight, BrFight.fight_id == FightKill.fight_id).where(
                BrFight.br_id == BattleReport.br_id
            )
        else:
            stmt = stmt.where(FightKill.fight_id == Fight.fight_id)
        return stmt.exists()

    clauses = [
        _exists(Killmail, Alliance.alliance_id == Killmail.victim_alliance_id, Alliance),
        _exists(Killmail,
                Corporation.corporation_id == Killmail.victim_corporation_id, Corporation),
        _exists(KillmailAttacker, Alliance.alliance_id == KillmailAttacker.alliance_id, Alliance),
        _exists(KillmailAttacker,
                Corporation.corporation_id == KillmailAttacker.corporation_id, Corporation),
    ]
    return or_(*clauses)


def _compile_node_fight(node: dict[str, Any]) -> Any:
    # Group node
    if "op" in node and "clauses" in node:
        group_op = node["op"]
        if group_op not in ("and", "or"):
            raise FilterError(f"Unknown group op: {group_op!r}")
        clauses_raw = node.get("clauses")
        if not isinstance(clauses_raw, list) or len(clauses_raw) == 0:
            raise FilterError("Group node requires non-empty 'clauses' list")
        clauses = [_compile_node_fight(c) for c in clauses_raw]
        return and_(*clauses) if group_op == "and" else or_(*clauses)

    field = node.get("field")
    if not isinstance(field, str):
        raise FilterError("Missing or invalid 'field'")

    # Validate field is whitelisted (SECURITY: no raw field names reach DB)
    if field == "ship_count":
        return _compile_ship_count_leaf(node)

    if field == "entity_involved":
        return _entity_involved_clause(node, scope="fight")

    if field not in _FIGHT_FIELDS:
        raise FilterError(f"Unknown fight field: {field!r}")

    col = _FIGHT_FIELDS[field]
    op = node.get("op")
    if not isinstance(op, str):
        raise FilterError("Missing or invalid 'op'")
    value = node.get("value")

    # Field-specific op validation
    numeric_fields = (
        "isk_destroyed_total", "largest_side_pilots", "distinct_alliance_count", "system_id"
    )
    if field in numeric_fields:
        if op not in _NUMERIC_OPS:
            raise FilterError(f"Unknown op {op!r} for field {field!r}")
        return _apply_num_op(col, op, value)
    elif field == "capitals_involved":
        if op != "==":
            raise FilterError(f"Op {op!r} not valid for capitals_involved; use '=='")
        return col == value
    elif field == "started_at":
        if op not in _DATETIME_OPS:
            raise FilterError(f"Unknown datetime op {op!r}")
        if op == "between":
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise FilterError("'between' op requires a [low, high] list")
            lo, hi = value[0], value[1]
            return col.between(lo, hi)
        return _apply_num_op(col, op, value)

    raise FilterError(f"Unhandled field: {field!r}")


def _compile_ship_count_leaf(node: dict[str, Any]) -> Any:
    ship_name = node.get("ship")
    if not isinstance(ship_name, str):
        raise FilterError("ship_count leaf requires 'ship' (str)")
    count_threshold = node.get("count")
    if not isinstance(count_threshold, int):
        raise FilterError("ship_count leaf requires 'count' (int)")
    op = node.get("op")
    if not isinstance(op, str) or op not in {">=", "<=", ">", "<", "=="}:
        raise FilterError(f"Unknown op for ship_count: {op!r}")
    side = node.get("side", "any")
    if side not in {"friendly", "hostile", "any"}:
        raise FilterError(f"Unknown side: {side!r}")

    base_sq = (
        select(func.sum(FightShipCount.count).label("total"))
        .join(InventoryType, InventoryType.type_id == FightShipCount.ship_type_id)
        .join(FightSide, and_(
            FightSide.fight_id == FightShipCount.fight_id,
            FightSide.side_idx == FightShipCount.side_idx,
        ))
        .where(
            FightShipCount.fight_id == Fight.fight_id,
            func.lower(InventoryType.name) == ship_name.lower(),
        )
    )
    if side != "any":
        base_sq = base_sq.where(FightSide.side_kind == side)
    scalar_sq = base_sq.scalar_subquery()

    coalesced = func.coalesce(scalar_sq, 0)
    return _apply_num_op(coalesced, op, count_threshold)


def compile_fight_filter(tree: dict[str, Any]) -> Select:  # type: ignore[type-arg]
    where_clause = _compile_node_fight(tree)
    return select(Fight).where(where_clause)


def _compile_node_br(node: dict[str, Any]) -> Any:
    if "op" in node and "clauses" in node:
        group_op = node["op"]
        if group_op not in ("and", "or"):
            raise FilterError(f"Unknown group op: {group_op!r}")
        clauses_raw = node.get("clauses")
        if not isinstance(clauses_raw, list) or len(clauses_raw) == 0:
            raise FilterError("Group node requires non-empty 'clauses' list")
        clauses = [_compile_node_br(c) for c in clauses_raw]
        return and_(*clauses) if group_op == "and" else or_(*clauses)

    field = node.get("field")
    if not isinstance(field, str):
        raise FilterError("Missing or invalid 'field'")

    if field == "ship_fielded":
        return _compile_ship_fielded_leaf(node)

    if field == "entity_involved":
        return _entity_involved_clause(node, scope="br")

    if field not in _BR_FIELDS:
        raise FilterError(f"Unknown BR field: {field!r}")

    col = _BR_FIELDS[field]
    op = node.get("op")
    if not isinstance(op, str):
        raise FilterError("Missing or invalid 'op'")
    value = node.get("value")

    if field in ("our_isk_destroyed", "our_isk_lost", "isk_efficiency", "fight_count"):
        if op not in _NUMERIC_OPS:
            raise FilterError(f"Unknown op {op!r} for field {field!r}")
        return _apply_num_op(col, op, value)
    elif field in ("result", "source"):
        if op not in _ENUM_OPS:
            raise FilterError(f"Unknown op {op!r} for field {field!r}")
        if op == "==":
            return col == value
        else:  # in
            if not isinstance(value, list):
                raise FilterError("'in' op requires a list value")
            return col.in_(value)
    elif field == "battle_at":
        if op not in _DATETIME_OPS:
            raise FilterError(f"Unknown datetime op {op!r}")
        if op == "between":
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                raise FilterError("'between' op requires a [low, high] list")
            lo, hi = value[0], value[1]
            return col.between(lo, hi)
        return _apply_num_op(col, op, value)

    raise FilterError(f"Unhandled BR field: {field!r}")


def _compile_ship_fielded_leaf(node: dict[str, Any]) -> Any:
    ship_name = node.get("ship")
    if not isinstance(ship_name, str):
        raise FilterError("ship_fielded leaf requires 'ship' (str)")
    count_threshold = node.get("count")
    if not isinstance(count_threshold, int):
        raise FilterError("ship_fielded leaf requires 'count' (int)")
    op = node.get("op")
    if not isinstance(op, str) or op not in {">=", "<=", ">", "<", "=="}:
        raise FilterError(f"Unknown op for ship_fielded: {op!r}")
    side = node.get("side", "any")
    if side not in {"friendly", "any"}:
        raise FilterError(f"Unknown side for ship_fielded: {side!r}")

    base_sq2 = (
        select(func.sum(BrShipCount.count).label("total"))
        .join(InventoryType, InventoryType.type_id == BrShipCount.ship_type_id)
        .where(
            BrShipCount.br_id == BattleReport.br_id,
            func.lower(InventoryType.name) == ship_name.lower(),
        )
    )
    if side != "any":
        base_sq2 = base_sq2.where(BrShipCount.side_kind == side)
    scalar_sq2 = base_sq2.scalar_subquery()

    coalesced = func.coalesce(scalar_sq2, 0)
    return _apply_num_op(coalesced, op, count_threshold)


def compile_br_filter(tree: dict[str, Any]) -> Select:  # type: ignore[type-arg]
    where_clause = _compile_node_br(tree)
    return select(BattleReport).where(where_clause)
