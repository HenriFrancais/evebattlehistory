# BR Detail Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load the full official-CCP SDE type catalogue (build-number cached), fix the gamelog parser so EWAR/cap/rep targets split into `Character (Ship)`, re-parse stored logs, and polish the snapshot (Shift-drag, UTC axis, full-height effect clusters) and per-character kills (link to zKill).

**Architecture:** A new `app/sde/` package downloads + processes the CCP SDE JSONL into a compact artifact (build-number cached, baked at image build) and loads it into the existing `InventoryType` columns at startup. A pure `split_entity` helper, driven by the SDE ship-name set, replaces the parser's brittle per-effect encoding guess; ingest applies it and a re-parse script replays it over stored logs. Frontend gets a Shift-drag snapshot gesture, a UTC axis formatter, an effect-clustered full-height snapshot panel, and clickable lost-ship rows.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / httpx / pytest (backend); React 18 / TypeScript / Vite / Vitest / uPlot (frontend).

## Global Constraints

- All dates `YYYY-MM-DD`; all times 24h UTC via `frontend/src/format.ts` helpers (`fmtTime`/`fmtDateTime`/`fmtCompact`). Never `toLocale*`.
- SDE source is the **official CCP** Static Data Export: manifest `https://developers.eveonline.com/static-data/tranquility/latest.jsonl` (`{"buildNumber": int}`); full export `https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip`. Re-download only when `buildNumber` advances or `--force`. Send a descriptive `User-Agent`.
- `InventoryType` already has `type_id, name, group_id, group_name, category_id, category_name, market_group_id` — populate them, do NOT add columns. `init_models` uses `create_all` (no migrations); new tables are auto-created, existing tables are not altered.
- Ship-like entity names = `InventoryType` rows with `category_id` in {6 (Ship), 11 (Entity/NPC)}.
- Composition side classification stays on `classify_entity`; `user_name`/By-user stays FC/HC-gated (unchanged).
- Backend tests use `db_session_maker` / `make_client` + `CREATOR_HEADERS`/`MEMBER_HEADERS` from `tests/conftest.py`; SDE/network is fixture-driven (no real downloads in tests). Run `uv run pytest`. Frontend tests mock `../api` and `uplot`; run `cd frontend && npm test`, `npx tsc --noEmit`.

---

### Task 1: `split_entity` — SDE-dictionary target splitter (pure)

**Files:**
- Create: `app/logs/entity.py`
- Create: `tests/test_entity_split.py`

**Interfaces:**
- Produces: `split_entity(text: str, entity_names: frozenset[str]) -> tuple[str | None, str | None]` returning `(character_name, ship_name)`. Consumed by Tasks 5, 6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity_split.py`:

```python
from app.logs.entity import split_entity

SHIPS = frozenset({"Guardian", "Scorpion", "Tempest Fleet Issue", "Bhaalgorn", "Arithmos Tyrannos"})


def test_player_ship_prefix():
    # "ShipType CharacterName [CORP] <ALLI>" → split on the leading ship token
    assert split_entity("Guardian Jennifer Hibra [NVACA] <NV>", SHIPS) == ("Jennifer Hibra", "Guardian")


def test_multiword_ship_prefix():
    assert split_entity("Tempest Fleet Issue Bob Smith [X] <Y>", SHIPS) == ("Bob Smith", "Tempest Fleet Issue")


def test_html_encoded_tickers_stripped():
    assert split_entity("Guardian Faith Hibra [NVACA] &lt;NV&gt;", SHIPS) == ("Faith Hibra", "Guardian")


def test_new_encoding_ship_suffix():
    # "CharacterName [CORP][ALLI] ShipType" → ship is the trailing token
    assert split_entity("Alan Bell [URSA][URSA.] Scorpion", SHIPS) == ("Alan Bell", "Scorpion")


def test_npc_bare_name():
    # Whole cleaned string is itself an entity name → NPC, no character
    assert split_entity("Arithmos Tyrannos", SHIPS) == (None, "Arithmos Tyrannos")
    assert split_entity("Bhaalgorn", SHIPS) == (None, "Bhaalgorn")


def test_unknown_no_ship():
    assert split_entity("Totally Unknown Pilot [X]", SHIPS) == ("Totally Unknown Pilot", None)


def test_empty():
    assert split_entity("", SHIPS) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_entity_split.py -q`
Expected: FAIL — `ModuleNotFoundError: app.logs.entity`.

- [ ] **Step 3: Implement**

Create `app/logs/entity.py`:

```python
"""Split a gamelog target string into (character, ship) using the SDE ship-name set.

EVE logs non-damage targets in inconsistent layouts:
  - player:  "ShipType CharacterName [CORP] <ALLI>"  (ship concatenated, no delimiter)
  - new enc: "CharacterName [CORP][ALLI] ShipType"   (ship trailing)
  - NPC:     "Bhaalgorn"                              (bare type name, no character)
The reliable discriminator is the ship-type name itself, so we match against the SDE
ship/entity name dictionary rather than guessing an encoding.
"""

from __future__ import annotations

import re

# Strip corp [TICKER], alliance <TICKER> / &lt;TICKER&gt;, and any [bracket] groups.
_TICKER_RE = re.compile(r"&lt;[^&]*&gt;|<[^>]*>|\[[^\]]*\]")


def _clean(text: str) -> str:
    s = _TICKER_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", s).strip()


def split_entity(text: str, entity_names: frozenset[str]) -> tuple[str | None, str | None]:
    """Return (character_name, ship_name). See module docstring."""
    cleaned = _clean(text or "")
    if not cleaned:
        return (None, None)
    if cleaned in entity_names:
        return (None, cleaned)  # bare NPC / ship name, no character

    words = cleaned.split(" ")
    # Longest leading run that is a known ship name → "ShipType CharacterName".
    for n in range(len(words) - 1, 0, -1):
        cand = " ".join(words[:n])
        if cand in entity_names:
            char = " ".join(words[n:]).strip()
            return (char or None, cand)
    # Longest trailing run that is a known ship name → "CharacterName ShipType".
    for n in range(len(words) - 1, 0, -1):
        cand = " ".join(words[len(words) - n:])
        if cand in entity_names:
            char = " ".join(words[: len(words) - n]).strip()
            return (char or None, cand)
    return (cleaned, None)  # unknown: character only
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_entity_split.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/logs/entity.py tests/test_entity_split.py
git commit -m "feat(logs): SDE-dictionary target splitter (split_entity)"
```

---

### Task 2: SDE artifact processing (pure JSONL → compact rows)

**Files:**
- Create: `app/sde/__init__.py`, `app/sde/process.py`
- Create: `tests/test_sde_process.py`

**Interfaces:**
- Produces: `process_sde_lines(types_lines: Iterable[str], groups_lines: Iterable[str]) -> list[dict]` → published types as `{"type_id", "name", "group_id", "group_name", "category_id", "category_name"}`. Consumed by Tasks 3, 4. Also `read_manifest_build(text: str) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sde_process.py`:

```python
from app.sde.process import process_sde_lines, read_manifest_build


def test_read_manifest_build():
    assert read_manifest_build('{"buildNumber": 2812345, "releaseDate": "2026-06-01"}') == 2812345
    assert read_manifest_build("not json") is None


def test_process_filters_published_and_joins_groups():
    # CCP JSONL: one object per line. id may be "_key" or "typeID"; name may be {"en":..} or str.
    types = [
        '{"_key": 2488, "groupID": 53, "name": {"en": "Dual 150mm Railgun II"}, "published": true}',
        '{"_key": 999, "groupID": 53, "name": {"en": "Unpublished Thing"}, "published": false}',
        '{"typeID": 670, "groupID": 29, "name": {"en": "Capsule"}, "published": true}',
    ]
    groups = [
        '{"_key": 53, "categoryID": 7, "name": {"en": "Energy Weapon"}}',
        '{"_key": 29, "categoryID": 6, "name": {"en": "Capsule"}}',
    ]
    out = {r["type_id"]: r for r in process_sde_lines(types, groups)}
    assert 999 not in out  # unpublished dropped
    assert out[2488]["name"] == "Dual 150mm Railgun II"
    assert out[2488]["category_id"] == 7
    assert out[670]["category_id"] == 6 and out[670]["category_name"] == "Capsule"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sde_process.py -q`
Expected: FAIL — `ModuleNotFoundError: app.sde.process`.

- [ ] **Step 3: Implement**

Create `app/sde/__init__.py` (empty). Create `app/sde/process.py`:

```python
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
        out.append({
            "type_id": tid,
            "name": name,
            "group_id": gid,
            "group_name": ginfo.get("group_name", "Unknown") or "Unknown",
            "category_id": ginfo.get("category_id", 0),
            "category_name": "",  # category name not needed; left blank
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sde_process.py -q`
Expected: PASS (2 tests).

> Format note: the `_id`/`_name` helpers tolerate `_key`/`typeID` and `{"en":..}`/string. During Task 11 (real run) confirm one real `types.jsonl` line matches; if CCP uses different keys, extend `_id`/`_name` accordingly.

- [ ] **Step 5: Commit**

```bash
git add app/sde/__init__.py app/sde/process.py tests/test_sde_process.py
git commit -m "feat(sde): pure CCP SDE JSONL processing + manifest parse"
```

---

### Task 3: SDE refresh script (download + build-number cache)

**Files:**
- Create: `app/sde/refresh.py`
- Create: `tests/test_sde_refresh.py`

**Interfaces:**
- Consumes: `process_sde_lines`, `read_manifest_build` (Task 2).
- Produces: `refresh_sde(sde_dir: Path, *, force=False, fetch_manifest, fetch_zip) -> int | None` — returns the build number written (or None if skipped/unchanged). `fetch_manifest`/`fetch_zip` are injected callables (real HTTP in `__main__`, fakes in tests). Writes `<sde_dir>/inventory_types.jsonl` + `<sde_dir>/manifest.json`. Consumed by Task 4 + Task 11.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sde_refresh.py`:

```python
import io
import json
import zipfile
from pathlib import Path

from app.sde.refresh import refresh_sde


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("types.jsonl", '{"_key":670,"groupID":29,"name":{"en":"Capsule"},"published":true}\n')
        z.writestr("groups.jsonl", '{"_key":29,"categoryID":6,"name":{"en":"Capsule"}}\n')
    return buf.getvalue()


def test_refresh_downloads_when_build_advances(tmp_path: Path):
    n = refresh_sde(tmp_path, fetch_manifest=lambda: '{"buildNumber": 100}', fetch_zip=_zip_bytes)
    assert n == 100
    rows = [json.loads(x) for x in (tmp_path / "inventory_types.jsonl").read_text().splitlines()]
    assert rows[0]["type_id"] == 670 and rows[0]["category_id"] == 6
    assert json.loads((tmp_path / "manifest.json").read_text())["buildNumber"] == 100


def test_refresh_skips_when_unchanged(tmp_path: Path):
    (tmp_path / "manifest.json").write_text('{"buildNumber": 100}')
    (tmp_path / "inventory_types.jsonl").write_text("")
    calls = []
    n = refresh_sde(tmp_path, fetch_manifest=lambda: '{"buildNumber": 100}',
                    fetch_zip=lambda: calls.append(1) or b"")
    assert n is None and calls == []  # no download


def test_force_redownloads(tmp_path: Path):
    (tmp_path / "manifest.json").write_text('{"buildNumber": 100}')
    n = refresh_sde(tmp_path, force=True, fetch_manifest=lambda: '{"buildNumber": 100}', fetch_zip=_zip_bytes)
    assert n == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sde_refresh.py -q`
Expected: FAIL — `ModuleNotFoundError: app.sde.refresh`.

- [ ] **Step 3: Implement**

Create `app/sde/refresh.py`:

```python
"""Download + process the CCP SDE, cached by build number. Network is injected so
the core is testable; the __main__ entrypoint wires real httpx."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

from app.observability.logging import log
from app.sde.process import process_sde_lines, read_manifest_build

MANIFEST_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
ZIP_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"
USER_AGENT = "nv-br (NV Tools; contact admin)"


def _cached_build(sde_dir: Path) -> int | None:
    mf = sde_dir / "manifest.json"
    if not mf.exists():
        return None
    return read_manifest_build(mf.read_text())


def refresh_sde(
    sde_dir: Path,
    *,
    force: bool = False,
    fetch_manifest: Callable[[], str],
    fetch_zip: Callable[[], bytes],
) -> int | None:
    """Refresh the processed SDE artifact if the build advanced (or force). Returns the
    new build number, or None when skipped/unchanged."""
    sde_dir.mkdir(parents=True, exist_ok=True)
    latest = read_manifest_build(fetch_manifest())
    if latest is None:
        log.warning("sde.manifest_unreadable")
        return None
    if not force and _cached_build(sde_dir) == latest:
        log.info("sde.up_to_date", build=latest)
        return None

    raw = fetch_zip()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        types_lines = z.read("types.jsonl").decode("utf-8").splitlines()
        groups_lines = z.read("groups.jsonl").decode("utf-8").splitlines()
    rows = process_sde_lines(types_lines, groups_lines)
    with (sde_dir / "inventory_types.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    (sde_dir / "manifest.json").write_text(json.dumps({"buildNumber": latest}))
    log.info("sde.processed", build=latest, types=len(rows))
    return latest


def _http_get_text(url: str) -> str:
    import httpx

    return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0,
                     follow_redirects=True).raise_for_status().text


def _http_get_bytes(url: str) -> bytes:
    import httpx

    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=120.0,
                      follow_redirects=True) as r:
        r.raise_for_status()
        return b"".join(r.iter_bytes())


if __name__ == "__main__":  # pragma: no cover
    import sys

    from app.config import get_settings

    force = "--force" in sys.argv
    sde_dir = get_settings().sde_dir
    build = refresh_sde(sde_dir, force=force,
                        fetch_manifest=lambda: _http_get_text(MANIFEST_URL),
                        fetch_zip=lambda: _http_get_bytes(ZIP_URL))
    print(f"SDE build: {build if build is not None else 'unchanged'} → {sde_dir}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sde_refresh.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/sde/refresh.py tests/test_sde_refresh.py
git commit -m "feat(sde): build-number-cached refresh (download + process)"
```

---

### Task 4: SDE runtime load into InventoryType + startup wiring

**Files:**
- Create: `app/sde/load.py`
- Modify: `app/db/models.py` (add `SdeMeta` table)
- Modify: `app/main.py` (lifespan: load SDE after `init_models`)
- Create: `tests/test_sde_load.py`

**Interfaces:**
- Consumes: the processed `inventory_types.jsonl` + `manifest.json` (Task 3).
- Produces: `async def load_sde_into_db(session, sde_dir) -> int` — upserts the artifact into `InventoryType` when the DB build is behind; returns rows upserted (0 if current/missing). `async def entity_name_set(session) -> frozenset[str]` — ship/entity (category 6/11) names. Consumed by Tasks 5, 6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sde_load.py`:

```python
import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_load_upserts_and_is_idempotent(db_session_maker, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import InventoryType
    from app.sde.load import load_sde_into_db, entity_name_set
    from sqlalchemy import select

    (tmp_path / "manifest.json").write_text(json.dumps({"buildNumber": 200}))
    (tmp_path / "inventory_types.jsonl").write_text(
        json.dumps({"type_id": 645, "name": "Dominix", "group_id": 27,
                    "group_name": "Battleship", "category_id": 6, "category_name": ""}) + "\n"
        + json.dumps({"type_id": 2488, "name": "Dual 150mm Railgun II", "group_id": 53,
                      "group_name": "Energy Weapon", "category_id": 7, "category_name": ""}) + "\n"
    )

    async with db_session_maker() as session:
        n = await load_sde_into_db(session, tmp_path)
        await session.commit()
    assert n == 2

    async with db_session_maker() as session:
        again = await load_sde_into_db(session, tmp_path)   # same build → skip
        await session.commit()
    assert again == 0

    async with db_session_maker() as session:
        names = await entity_name_set(session)
        row = (await session.execute(select(InventoryType).where(InventoryType.type_id == 645))).scalar_one()
    assert "Dominix" in names          # category 6 ship
    assert "Dual 150mm Railgun II" not in names  # category 7 weapon, not a ship
    assert row.name == "Dominix" and row.category_id == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sde_load.py -q`
Expected: FAIL — `ModuleNotFoundError: app.sde.load`.

- [ ] **Step 3: Add the `SdeMeta` table**

In `app/db/models.py`, add (near the other small tables):

```python
class SdeMeta(Base):
    __tablename__ = "sde_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    build_number: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: Implement the loader**

Create `app/sde/load.py`:

```python
"""Load the processed SDE artifact into InventoryType, keyed by build number."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryType, SdeMeta
from app.observability.logging import log
from app.sde.process import read_manifest_build

SHIP_LIKE_CATEGORIES = {6, 11}  # Ship, Entity(NPC)


async def load_sde_into_db(session: AsyncSession, sde_dir: Path) -> int:
    """Upsert inventory_types.jsonl into InventoryType when the DB build is behind.
    Returns the number of rows upserted (0 if current or artifact missing)."""
    mf = sde_dir / "manifest.json"
    art = sde_dir / "inventory_types.jsonl"
    if not mf.exists() or not art.exists():
        return 0
    build = read_manifest_build(mf.read_text())
    if build is None:
        return 0
    meta = (await session.execute(select(SdeMeta).where(SdeMeta.id == 1))).scalar_one_or_none()
    if meta is not None and meta.build_number == build:
        return 0

    rows = [json.loads(line) for line in art.read_text().splitlines() if line.strip()]
    # SQLite caps bound variables per statement (~999 / 32766); chunk to stay safe.
    CHUNK = 2000
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        cstmt = sqlite_insert(InventoryType).values([
            {"type_id": r["type_id"], "name": r["name"], "group_id": r["group_id"],
             "group_name": r["group_name"], "category_id": r["category_id"],
             "category_name": r.get("category_name", "")}
            for r in chunk
        ])
        cstmt = cstmt.on_conflict_do_update(
            index_elements=["type_id"],
            set_={"name": cstmt.excluded.name, "group_id": cstmt.excluded.group_id,
                  "group_name": cstmt.excluded.group_name,
                  "category_id": cstmt.excluded.category_id},
        )
        await session.execute(cstmt)

    await session.merge(SdeMeta(id=1, build_number=build))
    log.info("sde.loaded", build=build, types=len(rows))
    return len(rows)


async def entity_name_set(session: AsyncSession) -> frozenset[str]:
    """Ship/entity (category 6/11) type names — the split_entity dictionary."""
    names = (
        await session.execute(
            select(InventoryType.name).where(InventoryType.category_id.in_(SHIP_LIKE_CATEGORIES))
        )
    ).scalars()
    return frozenset(n for n in names if n)
```

- [ ] **Step 5: Wire into lifespan**

In `app/main.py` `lifespan`, after `await init_models(settings)` add:

```python
    try:
        from app.db.engine import get_sessionmaker
        from app.sde.load import load_sde_into_db

        async with get_sessionmaker(settings)() as _s:
            loaded = await load_sde_into_db(_s, settings.sde_dir)
            await _s.commit()
        if loaded:
            log.info("sde.startup_loaded", types=loaded)
    except Exception as exc:
        log.warning("sde.startup_load_failed", error=str(exc))
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_sde_load.py -q`
Expected: PASS (idempotent load; entity set filters to ship categories).

- [ ] **Step 7: Commit**

```bash
git add app/sde/load.py app/db/models.py app/main.py tests/test_sde_load.py
git commit -m "feat(sde): runtime load into InventoryType + startup wiring"
```

---

### Task 5: Apply `split_entity` in ingest

**Files:**
- Modify: `app/logs/ingest.py`
- Modify: `tests/test_log_ingest.py` (add a split assertion)

**Interfaces:**
- Consumes: `split_entity` (Task 1), `entity_name_set` (Task 4).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_log_ingest.py` (match its existing fixture/helpers; this seeds a ship name then ingests a line the parser leaves merged):

```python
@pytest.mark.asyncio
async def test_ingest_splits_merged_target(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.models import InventoryType, LogEvent
    from app.logs.ingest import ingest_log
    from sqlalchemy import select

    raw = (
        "[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        "Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact Remote Capacitor Transmitter\n"
    ).encode()

    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        await session.flush()
        await ingest_log(session, get_settings(), "u", "Listener_20260614_205700_90000001.txt",
                         raw, lambda n: 90000001)
        await session.commit()

    async with db_session_maker() as session:
        ev = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "cap_transfer")
        )).scalars().first()
    assert ev is not None
    assert ev.other_ship_name == "Guardian"
    assert ev.other_name == "Jennifer Hibra"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_log_ingest.py::test_ingest_splits_merged_target -q`
Expected: FAIL — `other_ship_name` is None / `other_name` is the merged string.

- [ ] **Step 3: Apply the split in ingest**

In `app/logs/ingest.py`, import the helpers at the top:

```python
from app.logs.entity import split_entity
from app.sde.load import entity_name_set
```

In `ingest_log`, before building the `LogEvent` rows (just before the `if parsed.events:` block at line ~160), load the entity dictionary once and split unsplit non-damage events:

```python
    # Recover Character (Ship) for non-damage targets the parser left merged, using
    # the SDE ship-name dictionary. Damage already splits (ship in parens).
    entity_names = await entity_name_set(session)
    for e in parsed.events:
        if e.effect_type and e.effect_type != "damage" and not e.other_ship_name and e.other_name:
            char, ship = split_entity(e.other_name, entity_names)
            object.__setattr__(e, "other_name", char if char is not None else e.other_name)
            object.__setattr__(e, "other_ship_name", ship)
```

(`ParsedLogEvent` is a frozen dataclass, hence `object.__setattr__`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_log_ingest.py::test_ingest_splits_merged_target -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/logs/ingest.py tests/test_log_ingest.py
git commit -m "feat(logs): split Character (Ship) targets at ingest via SDE dictionary"
```

---

### Task 6: Re-parse stored gamelogs

**Files:**
- Create: `app/logs/reparse.py`
- Create: `tests/test_reparse.py`

**Interfaces:**
- Consumes: `parse_log`, `split_entity`, `entity_name_set`, `associate_file_to_all`.
- Produces: `async def reparse_gamelogs(session, settings) -> int` — re-parses each `GamelogFile` with a readable `stored_path`, replaces its `LogEvent` rows (split applied), re-stamps via `associate_file_to_all`; returns files re-parsed. Runnable via `python -m app.logs.reparse`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reparse.py`:

```python
import datetime as dt
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_reparse_replaces_events_with_split(db_session_maker, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings
    from app.db.models import GamelogFile, InventoryType, LogEvent
    from app.logs.reparse import reparse_gamelogs
    from sqlalchemy import select

    p = tmp_path / "g.txt"
    p.write_text(
        "[ 2026.06.14 20:57:34 ] (combat) 88 remote capacitor transmitted to "
        "Guardian Jennifer Hibra [NVACA] &lt;NV&gt; - Large Inductive Compact Remote Capacitor Transmitter\n",
        encoding="utf-8",
    )
    async with db_session_maker() as session:
        session.add(InventoryType(type_id=11987, name="Guardian", category_id=6))
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=90000001, resolved_via="filename",
                         stored_path=str(p), sha256="rr", mime="text/plain", size=1,
                         parse_status="parsed", event_count=0, uploaded_at=dt.datetime.now(dt.UTC))
        session.add(gf)
        # a stale event that must be replaced
        await session.flush()
        session.add(LogEvent(file_id=gf.file_id, character_id=90000001,
                             ts=dt.datetime(2026, 6, 14, 20, 57, 34), effect_type="cap_transfer",
                             direction="out", other_name="STALE", other_ship_name=None))
        await session.commit()

    async with db_session_maker() as session:
        n = await reparse_gamelogs(session, get_settings())
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        evs = (await session.execute(select(LogEvent))).scalars().all()
    assert all(e.other_name != "STALE" for e in evs)
    cap = next(e for e in evs if e.effect_type == "cap_transfer")
    assert cap.other_ship_name == "Guardian" and cap.other_name == "Jennifer Hibra"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_reparse.py -q`
Expected: FAIL — `ModuleNotFoundError: app.logs.reparse`.

- [ ] **Step 3: Implement**

Create `app/logs/reparse.py`:

```python
"""Re-parse already-ingested gamelog files with the fixed parser + SDE splitter.

Replaces each file's LogEvent rows in place (delete + re-insert under the same file_id)
and re-stamps them to fights. Uses the stored file on disk — no re-upload needed.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import GamelogFile, LogEvent
from app.logs.associate import associate_file_to_all
from app.logs.entity import split_entity
from app.logs.parse import parse_log
from app.observability.logging import log
from app.sde.load import entity_name_set


async def reparse_gamelogs(session: AsyncSession, settings: Settings) -> int:
    """Re-parse every GamelogFile with a readable stored file. Returns count re-parsed."""
    entity_names = await entity_name_set(session)
    files = list((await session.execute(select(GamelogFile))).scalars())
    done = 0
    for gf in files:
        try:
            text = Path(gf.stored_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("reparse.file_unreadable", file_id=gf.file_id, error=str(exc))
            continue
        parsed = parse_log(text)
        await session.execute(delete(LogEvent).where(LogEvent.file_id == gf.file_id))
        rows: list[LogEvent] = []
        for e in parsed.events:
            other_name, other_ship = e.other_name, e.other_ship_name
            if e.effect_type and e.effect_type != "damage" and not other_ship and other_name:
                char, ship = split_entity(other_name, entity_names)
                other_name = char if char is not None else other_name
                other_ship = ship
            rows.append(LogEvent(
                file_id=gf.file_id, character_id=gf.claimed_character_id, ts=e.ts,
                direction=e.direction, effect_type=e.effect_type, amount=e.amount,
                quality=e.quality, other_name=other_name, other_corp_ticker=e.other_corp_ticker,
                other_alliance_ticker=e.other_alliance_ticker, other_ship_name=other_ship,
                module_name=e.module_name, fight_id=None,
            ))
        if rows:
            session.add_all(rows)
        gf.event_count = len(rows)
        await session.flush()
        await associate_file_to_all(session, gf.file_id)
        done += 1
    log.info("reparse.done", files=done)
    return done


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    from app.config import get_settings
    from app.db.engine import get_sessionmaker

    async def _main() -> None:
        settings = get_settings()
        async with get_sessionmaker(settings)() as session:
            n = await reparse_gamelogs(session, settings)
            await session.commit()
        print(f"re-parsed {n} gamelog files")

    asyncio.run(_main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_reparse.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/logs/reparse.py tests/test_reparse.py
git commit -m "feat(logs): re-parse stored gamelogs with the SDE splitter"
```

---

### Task 7: Composition lost-hull killmail_id

**Files:**
- Modify: `app/analytics/composition.py`
- Modify: `app/api/schemas.py` (`CompositionPilotOut`)
- Modify: `app/api/fleet.py` (composition mapping)
- Modify: `frontend/src/api.ts` (`CompositionPilot`)
- Modify: `tests/test_composition.py`

**Interfaces:**
- Produces: `CompositionPilot.killmail_id: int | None` (set for the lost hull); `CompositionPilotOut` + frontend type gain it. Consumed by Task 8.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_composition.py`:

```python
@pytest.mark.asyncio
async def test_composition_lost_hull_has_killmail_id(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # VICTIM lost ship type 1 ("TestShip") on a km
        km_id = (await session.execute(
            select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
        )).scalar_one()
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    victim = next(p for s in result.sides for p in s.pilots if p.character_id == VICTIM)
    assert victim.lost is True and victim.killmail_id == km_id
    attacker = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert attacker.killmail_id is None  # not lost
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py::test_composition_lost_hull_has_killmail_id -q`
Expected: FAIL — `CompositionPilot` has no `killmail_id`.

- [ ] **Step 3: Track the loss killmail in composition**

In `app/analytics/composition.py`:

Add `killmail_id: int | None` to `CompositionPilot` (after `reship`).

Change `_Acc.hulls` to also remember the killmail id of a lost hull. Replace the `_Acc` dataclass's `hulls` typing and the victim loop to store `(lost, km_id)`:

```python
@dataclass
class _Acc:
    side: str
    hulls: dict[int, tuple[bool, int | None]]  # ship_type_id → (lost?, killmail_id)
    podded: bool
```

In the victim loop, select the killmail id too and store it:

```python
    for km_id, char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                Killmail.killmail_id,
                Killmail.victim_character_id,
                Killmail.victim_ship_type_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        a = _ensure(char_id, _side(alli, corp))
        a.side = _side(alli, corp)
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls[ship_id] = (True, km_id)
        elif ship_id == CAPSULE_TYPE_ID:
            a.podded = True
```

In the attacker loop, store non-lost hulls as `(False, None)`:

```python
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls.setdefault(ship_id, (False, None))
```

In the pilot-build loop, unpack `(lost, km_id)` and set `killmail_id`:

```python
        if a.hulls:
            for sid, (lost, km_id) in a.hulls.items():
                by_side.setdefault(a.side, []).append(
                    CompositionPilot(character_id=char_id, character_name=name, ship_type_id=sid,
                                     ship_name=ship_names.get(sid, "Unknown"), lost=lost,
                                     reship=is_reship, user_name=user, killmail_id=km_id)
                )
        else:
            by_side.setdefault(a.side, []).append(
                CompositionPilot(character_id=char_id, character_name=name, ship_type_id=None,
                                 ship_name="Unknown", lost=a.podded, reship=False,
                                 user_name=user, killmail_id=None)
            )
```

- [ ] **Step 4: Schema + endpoint + frontend type**

In `app/api/schemas.py`, add `killmail_id: int | None = None` to `CompositionPilotOut` (after `reship`).
In `app/api/fleet.py`, the `CompositionPilotOut(...)` mapping adds `killmail_id=p.killmail_id`.
In `frontend/src/api.ts`, add `killmail_id: number | null` to `CompositionPilot` (after `reship`).

- [ ] **Step 5: Run tests + typecheck**

Run: `uv run pytest tests/test_composition.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add app/analytics/composition.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_composition.py
git commit -m "feat: composition lost-hull carries its killmail_id"
```

---

### Task 8: Clickable lost-ship rows (FleetsPanel)

**Files:**
- Modify: `frontend/src/components/FleetsPanel.tsx`
- Modify: `frontend/src/components/FleetsPanel.test.tsx`

**Interfaces:**
- Consumes: `CompositionPilot.killmail_id` (Task 7).

- [ ] **Step 1: Add a failing test**

Append to `frontend/src/components/FleetsPanel.test.tsx` (existing fixtures gain `killmail_id: null`):

```tsx
  it('links a lost pilot ship to zKillboard', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 645, ship_name: 'Dominix', count: 1 }],
        pilots: [{ character_id: 7, character_name: 'Vic', ship_type_id: 645, ship_name: 'Dominix',
                   lost: true, reship: false, user_name: null, killmail_id: 12345 }] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))
    const link = screen.getByRole('link', { name: /lost/i })
    expect(link).toHaveAttribute('href', 'https://zkillboard.com/kill/12345/')
  })
```

(Add `killmail_id: null` to the other pilot fixtures in this file so types compile.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx`
Expected: FAIL — no link rendered.

- [ ] **Step 3: Render the loss as a link**

In `frontend/src/components/FleetsPanel.tsx` `PilotRow`, replace the `✗` loss marker with a zKill link when `killmail_id` is present:

```tsx
        {p.lost && p.killmail_id != null ? (
          <a className="comp-lost" href={`https://zkillboard.com/kill/${p.killmail_id}/`}
             target="_blank" rel="noopener noreferrer" title="lost ship — open on zKillboard"
             aria-label="lost ship"> ✗</a>
        ) : p.lost ? (
          <span className="comp-lost" title="lost ship"> ✗</span>
        ) : null}
```

- [ ] **Step 4: Run test + typecheck**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx && npx tsc --noEmit`
Expected: PASS; tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FleetsPanel.tsx frontend/src/components/FleetsPanel.test.tsx
git commit -m "feat(fe): per-character lost ships link to zKillboard"
```

---

### Task 9: Fleet-graph UTC axis

**Files:**
- Modify: `frontend/src/components/FleetGraph.tsx`

**Interfaces:** none new.

Read the current `FleetGraph.tsx` first.

- [ ] **Step 1: Add a UTC x-axis formatter**

In the `axes` array of the uPlot options (the x-axis is `axes[0]`), add a `values` formatter that renders UTC from the epoch-second tick values. The x-axis entry becomes:

```tsx
        {
          stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID },
          // UTC ticks: date on the day boundary, else HH:MM:SS. uPlot x values are epoch seconds.
          values: (_u, splits) => splits.map((v) => {
            const iso = new Date(v * 1000).toISOString()
            return iso.slice(11, 19) // HH:MM:SS UTC
          }),
        },
```

Keep the existing `scales: { x: { time: true } }`. The cursor/time-series value formatter (`new Date(v*1000).toISOString().slice(11,19)`) and kill tooltip already render UTC — leave them.

- [ ] **Step 2: Typecheck + tests**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: clean; suite still green (axis formatting isn't unit-tested under the uPlot mock — verified visually in Task 11).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/FleetGraph.tsx
git commit -m "feat(fe): UTC 24h x-axis on the fleet graph"
```

---

### Task 10: Snapshot gesture — Shift-drag (replace two-click)

**Files:**
- Modify: `frontend/src/components/FleetGraph.tsx`
- Modify: `frontend/src/components/FleetSection.test.tsx` (if it referenced two-click; otherwise no change)

**Interfaces:** `FleetGraph` keeps `selectedRange` / `onSelectRange`.

Read the current `FleetGraph.tsx` first. Replace the two-click protocol with Shift-drag.

- [ ] **Step 1: Remove two-click, add Shift-drag capture**

In `PanelChart`, remove the `onRangeClick`-on-`click` handler and the drag-distance guard. Add a capture-phase `mousedown` handler on `u.over` that, **only when `ev.shiftKey`**, takes over to paint a band (and prevents uPlot's native drag-zoom via `stopImmediatePropagation`); without Shift it does nothing (native drag-zoom / double-click-zoom run normally):

```tsx
    // Shift-drag paints the snapshot range; plain drag = zoom, double-click = zoom-out (native).
    const onShiftDown = (ev: MouseEvent) => {
      if (!ev.shiftKey) return
      ev.stopImmediatePropagation()   // block uPlot's drag-zoom
      ev.preventDefault()
      const rect = u.over.getBoundingClientRect()
      const from = u.posToVal(ev.clientX - rect.left, 'x')
      const move = (e: MouseEvent) => {
        const to = u.posToVal(e.clientX - rect.left, 'x')
        onRangeDrag({ from: Math.min(from, to), to: Math.max(from, to) })
      }
      const up = () => {
        document.removeEventListener('mousemove', move)
        document.removeEventListener('mouseup', up)
      }
      document.addEventListener('mousemove', move)
      document.addEventListener('mouseup', up)
    }
    u.over.addEventListener('mousedown', onShiftDown, true) // capture phase
```

`onRangeDrag` is the existing prop that sets `{from,to}` (the same callback the handle-drag uses). Remove `onRangeClick` from `PanelChartProps` and its usages; keep `onRangeDrag`. In `FleetGraph`, delete `handleRangeClick`/`pendingStartRef` and pass `handleRangeDrag` as `onRangeDrag`. The range band + draggable handles (rangePlugin) stay for fine-tuning.

- [ ] **Step 2: Update the empty-state copy**

In `SnapshotPanel.tsx` the empty hint should now read "Shift-drag a range on any graph to snapshot it." (the `moment-detail-empty` block). Update that string.

- [ ] **Step 3: Tests + typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS. (Shift-drag canvas behaviour isn't unit-tested under the uPlot mock — verified in Task 11. If `FleetSection.test.tsx` asserted the old two-click, drop that assertion.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/FleetGraph.tsx frontend/src/components/SnapshotPanel.tsx frontend/src/components/FleetSection.test.tsx
git commit -m "feat(fe): Shift-drag snapshot gesture (zoom/double-click untouched)"
```

---

### Task 11: Snapshot panel — full height, effect clusters, hover-expand

**Files:**
- Modify: `frontend/src/components/SnapshotPanel.tsx`
- Modify: `frontend/src/components/SnapshotPanel.test.tsx`
- Modify: `frontend/src/styles/app.css`

**Interfaces:** none new.

Read the current `SnapshotPanel.tsx` first.

- [ ] **Step 1: Update the test**

In `SnapshotPanel.test.tsx`, add an assertion that rows are clustered by effect family with summaries and no truncation. Replace/extend the busiest-first test to also assert clusters:

```tsx
  it('clusters by effect family with summaries; expands a cluster on demand', async () => {
    vi.mocked(api.snapshot).mockResolvedValue(resp) // resp has damage + rep_armor rows
    render(<SnapshotPanel brId="br1" range={{ from: 1000, to: 1010 }} />)
    await waitFor(() => expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument())
    expect(screen.getByTestId('cluster-damage')).toBeInTheDocument()
    expect(screen.getByTestId('cluster-reps')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Restructure the panel**

In `SnapshotPanel.tsx`, group rows into effect families before grouping by target, render a cluster per family with a summary header and its target cards, and drop the per-card "+12 more" truncation (show all rows). Add the family mapping and cluster rendering:

```tsx
const EFFECT_FAMILY: Record<string, string> = {
  damage: 'damage',
  rep_armor: 'reps', rep_shield: 'reps',
  neut: 'cap', nos: 'cap', cap_transfer: 'cap',
  scram: 'ewar', disrupt: 'ewar', jam: 'ewar',
}
const FAMILY_ORDER: { id: string; label: string }[] = [
  { id: 'damage', label: 'Damage' }, { id: 'reps', label: 'Reps' },
  { id: 'cap', label: 'Cap' }, { id: 'ewar', label: 'EWAR' },
]
```

Render: for each family in `FAMILY_ORDER` that has rows, a `<div className="snap-cluster" data-testid={`cluster-${id}`}>` with a summary line (`label · N targets · {fmtCompact(sum)}`) and, inside, the existing `groupByTarget(...)` target cards for that family's rows. Remove the `.slice(0, 12)` + "+N more" lines in `TargetCard` (show `group.rows` in full). Keep the `Character (Ship)` header and quality tags.

- [ ] **Step 3: Full-height + hover-expand CSS**

Append to `frontend/src/styles/app.css`:

```css
.snap-cluster { border: 1px solid var(--border); border-radius: 6px; margin-bottom: 0.5rem; overflow: hidden; }
.snap-cluster-head { padding: 0.35rem 0.5rem; font-size: 0.8rem; font-weight: 600; background: var(--panel-2); cursor: default; }
.snap-cluster-body { max-height: 0; overflow: hidden; transition: max-height 0.15s ease; }
.snap-cluster:hover .snap-cluster-body, .snap-cluster:focus-within .snap-cluster-body { max-height: none; padding: 0.4rem; }
.contrib-panel { display: flex; flex-direction: column; max-height: calc(100vh - 7rem); overflow-y: auto; }
.focus-list { overflow: visible; max-height: none; }
```

(Make the cluster body the hover/focus target; remove the previous `.focus-list { max-height: 30rem; overflow-y: auto }` rule if present so the panel — not each list — owns the scroll.)

- [ ] **Step 4: Tests + typecheck + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: PASS; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SnapshotPanel.tsx frontend/src/components/SnapshotPanel.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): full-height snapshot clustered by effect with hover-expand"
```

---

### Task 12: Dockerfile SDE build stage + dev-DB population + full verification

**Files:**
- Modify: `deploy/Dockerfile`
- (no test files — ops + verification)

- [ ] **Step 1: Add the SDE build stage to the Dockerfile**

In `deploy/Dockerfile`, in the Python runtime stage (after the app is copied), add a build step that processes the SDE over a BuildKit cache mount on the SDE dir and bakes the artifact in. Insert before the `USER appuser` line:

```dockerfile
# Process the CCP SDE once per build, cached by build number across builds.
ENV SDE_DIR=/app/var/sde
RUN --mount=type=cache,target=/app/var/sde \
    python -m app.sde.refresh \
    && mkdir -p /app/sde-baked \
    && cp /app/var/sde/inventory_types.jsonl /app/var/sde/manifest.json /app/sde-baked/ 2>/dev/null || true
```

(At runtime the compose `SDE_DIR` points at the baked copy, or the cache volume is mounted; the startup loader reads `$SDE_DIR`. Keep the existing runtime `SDE_DIR` env from `.env`/compose; ensure the baked files are on the path the runtime `SDE_DIR` resolves to — copy `sde-baked/*` into the runtime `SDE_DIR` in the CMD or set `SDE_DIR=/app/sde-baked`.)

- [ ] **Step 2: Populate the dev DB (real SDE) + re-parse**

Run against the live dev DB (`.env` `DB_PATH=./var/db/dev.db`, `SDE_DIR=./var/sde`):

```bash
uv run python -m app.sde.refresh        # downloads + processes the real CCP SDE (~80MB once)
uv run python -c "import asyncio; from app.config import get_settings; from app.db.engine import get_sessionmaker, init_models; from app.sde.load import load_sde_into_db
async def m():
    s=get_settings(); await init_models(s)
    async with get_sessionmaker(s)() as ses:
        print('loaded', await load_sde_into_db(ses, s.sde_dir)); await ses.commit()
asyncio.run(m())"
uv run python -m app.logs.reparse       # re-parse stored logs with the splitter
```

Expected: SDE build prints a number; load prints thousands of types; reparse prints the file count.

> Confirm a real `types.jsonl` line matches `process_sde_lines`' `_id`/`_name` assumptions; if `app.sde.refresh` produces 0 rows, inspect one line from the downloaded zip and extend `_id`/`_name` (Task 2), then re-run.

- [ ] **Step 3: Full backend suite + lint + types**

Run: `uv run pytest -q && uv run ruff check app tests && uv run mypy app`
Expected: pytest all pass; no NEW ruff/mypy errors in files this plan touched (pre-existing debt in untouched files may remain).

- [ ] **Step 4: Full frontend suite + types + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 5: Real-data smoke**

Restart the dev servers; on a real BR confirm: weapon icons resolve for every damage row (incl. faction charges); snapshot titles are `Character (Ship)` (or the NPC name) with no bare-ship titles; Shift-drag selects a snapshot range while plain drag zooms and double-click zooms out; the graph axis shows UTC times; the snapshot uses full height, clusters by Damage/Reps/Cap/EWAR, and expands on hover; lost ships in Per-character link to zKill.

- [ ] **Step 6: Commit**

```bash
git add deploy/Dockerfile
git commit -m "build: process CCP SDE at image build (build-number cached)"
```

---

## Self-Review

**Spec coverage:**
- (A) SDE loader (official CCP, build-number cached) → Tasks 2 (process), 3 (refresh+cache), 4 (runtime load + `sde_meta`), 12 (Dockerfile stage + dev-DB run). ✓
- (B) parser splitter → Task 1 (`split_entity`) + Task 5 (ingest integration). ✓
- (C) re-parse stored logs → Task 6. ✓
- (D) NPC titles → falls out of Tasks 5/6 (`split_entity` returns `(None, name)`); the frontend already renders bare name when ship is null. ✓
- (E) weapon icons → Tasks 2-4 populate `InventoryType`; existing exact-match resolution then covers all (verified Task 12). ✓
- (F) UTC axis → Task 9. ✓
- (G) Shift-drag → Task 10. ✓
- (H) full-height clustered snapshot → Task 11. ✓
- (I) clickable kills → Task 7 (`killmail_id`) + Task 8 (link). ✓

**Placeholder scan:** Task 12 Step 1 notes the runtime `SDE_DIR` path must resolve to the baked artifact (concrete options given). Task 2's format note and Task 12's verify-one-line step are real verification gates against CCP's JSONL field names (which I can't confirm offline), not placeholders. No TODO/TBD.

**Type consistency:** `split_entity(text, frozenset) -> (char|None, ship|None)` consistent across Tasks 1, 5, 6. `entity_name_set(session) -> frozenset[str]` and `load_sde_into_db(session, sde_dir) -> int` consistent across Tasks 4, 5, 6. `process_sde_lines`/`read_manifest_build` consistent across Tasks 2, 3, 4. `refresh_sde(sde_dir, *, force, fetch_manifest, fetch_zip)` consistent across Tasks 3, 12. `CompositionPilot.killmail_id: int | None` consistent across Task 7 (dataclass, schema, frontend) and Task 8 (link). `_Acc.hulls: dict[int, tuple[bool, int|None]]` is updated in every loop that touches it within Task 7. `onRangeDrag` (kept) vs `onRangeClick` (removed) consistent across Task 10.
