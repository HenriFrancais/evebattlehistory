"""Process the CCP SDE JSONL export into compact InventoryType rows.

Pure functions — no I/O. The CCP JSONL has one JSON object per line; the type id
appears as "_key" (new export) or "typeID", and names are {"en": "..."} or a bare
string. We keep published types only and join group → category.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def read_manifest_build(text: str) -> int | None:
    """Parse the ~200-byte manifest; return buildNumber or None."""
    try:
        return int(json.loads(text)["buildNumber"])
    except (ValueError, KeyError, TypeError):
        return None


def _id(obj: dict[str, Any]) -> int | None:
    v = obj.get("_key", obj.get("typeID", obj.get("groupID")))
    return int(v) if isinstance(v, (int, str)) and str(v).isdigit() else None


def _name(obj: dict[str, Any]) -> str:
    n = obj.get("name")
    if isinstance(n, dict):
        return str(n.get("en", "")).strip()
    return str(n or "").strip()


def process_sde_lines(types_lines: Iterable[str], groups_lines: Iterable[str]) -> list[dict]:
    """Return published types joined to their group/category."""
    groups: dict[int, dict] = {}
    for line in groups_lines:
        line = line.strip()
        if not line:
            continue
        try:
            g = json.loads(line)
        except ValueError:
            continue
        gid = _id(g)
        if gid is None:
            continue
        cat = g.get("categoryID")
        groups[gid] = {"category_id": int(cat) if isinstance(cat, int) else 0,
                       "group_name": _name(g)}

    out: list[dict] = []
    for line in types_lines:
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if not t.get("published"):
            continue
        tid = _id(t)
        name = _name(t)
        if tid is None or not name:
            continue
        gid = t.get("groupID")
        gid = int(gid) if isinstance(gid, int) else 0
        ginfo = groups.get(gid, {})
        # groups.jsonl carries categoryID but no category *name*; the group's own
        # name is the best human label available, so reuse it as category_name.
        group_name = ginfo.get("group_name", "Unknown") or "Unknown"
        out.append({
            "type_id": tid,
            "name": name,
            "group_id": gid,
            "group_name": group_name,
            "category_id": ginfo.get("category_id", 0),
            "category_name": group_name if ginfo else "",
        })
    return out
