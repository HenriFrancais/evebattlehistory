# BR Detail Two-Column Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the BR detail page into a two-column layout (dominant fleet graph + sticky detail rail), add a fleet-composition summary with Composition / Per-character / By-user toggle, show resolved weapon icons in the moment-detail panel, surface victim pilot names on kill markers, and standardise date/time to ISO + 24h UTC.

**Architecture:** Backend adds a pure weapon classifier and a composition analytics function + endpoint, and enriches the existing contributions/kills payloads. Frontend splits `FleetSection` into `FleetGraph` + `MomentDetailPanel`, adds a `FleetsPanel`, lifts the selected-moment state to `BrDetailPage`, and lays the page out as a two-column grid. Side classification everywhere reuses the existing `classify_entity` + per-BR overrides so composition stays consistent with kill markers.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / pytest (backend); React 18 / TypeScript / Vite / Vitest / Testing-Library / uPlot (frontend).

## Global Constraints

- All dates render as `YYYY-MM-DD`; all times render 24-hour relative to **UTC**, never browser locale. Use shared `frontend/src/format.ts` helpers.
- Side classification uses `app.analytics.sides_config.classify_entity` with baseline blues (`AppConfig.our_alliance_ids` / `our_corp_ids`) plus per-BR overrides from `load_overrides`. Never re-derive sides another way.
- char→user mapping (`user_name`) is sent **only** to elevated callers (`can_create_br` true). Non-elevated callers receive `user_name: null` and no "By user" tab.
- Composition is killmail-derived (attackers + victims); pilots not on any killmail are not shown. This matches the rest of the app.
- Backend tests use the `db_session_maker` / `make_client` fixtures and `CREATOR_HEADERS` / `MEMBER_HEADERS` from `tests/conftest.py`. Frontend component tests mock `../api` and `uplot` as in `FleetSection.test.tsx`.
- Run backend tests with `uv run pytest`; frontend tests with `npm test` (from `frontend/`, runs `vitest run`).

---

### Task 1: Date/time format helpers + sweep

**Files:**
- Modify: `frontend/src/format.ts`
- Create: `frontend/src/format.test.ts`
- Modify: `frontend/src/components/BrCard.tsx:16-19`, `frontend/src/components/FightList.tsx:23-25`, `frontend/src/views/FightDetailPage.tsx:234-238`, `frontend/src/views/CharacterTimelinePage.tsx:7-9`, `frontend/src/views/LogsPage.tsx:16-18`

**Interfaces:**
- Produces: `fmtDate(x)`, `fmtTime(x, withSeconds?)`, `fmtDateTime(x)` where `x: Date | number | string` (number = epoch **seconds**, string = ISO). Used by Tasks 6, 7, 8.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/format.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { fmtDate, fmtTime, fmtDateTime } from './format'

// 2026-06-16T19:21:14Z
const EPOCH_S = 1781897514 / 1 // overwritten below to a known instant
const ISO = '2026-06-16T19:21:14Z'
const EPOCH = Date.parse(ISO) / 1000

describe('date/time formatting (UTC)', () => {
  it('fmtDate → YYYY-MM-DD from ISO string', () => {
    expect(fmtDate(ISO)).toBe('2026-06-16')
  })
  it('fmtDate from epoch seconds', () => {
    expect(fmtDate(EPOCH)).toBe('2026-06-16')
  })
  it('fmtTime → HH:MM 24h UTC (no seconds by default)', () => {
    expect(fmtTime(ISO)).toBe('19:21')
  })
  it('fmtTime with seconds', () => {
    expect(fmtTime(EPOCH, true)).toBe('19:21:14')
  })
  it('fmtDateTime → YYYY-MM-DD HH:MM UTC', () => {
    expect(fmtDateTime(new Date(ISO))).toBe('2026-06-16 19:21')
  })
  void EPOCH_S
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/format.test.ts`
Expected: FAIL — `fmtDate is not a function` (only `fmtIsk` exists).

- [ ] **Step 3: Implement the helpers**

Append to `frontend/src/format.ts`:

```ts
/** Coerce Date | epoch-seconds | ISO string to a Date. */
function toDate(x: Date | number | string): Date {
  if (x instanceof Date) return x
  if (typeof x === 'number') return new Date(x * 1000)
  return new Date(x)
}

/** ISO calendar date in UTC: YYYY-MM-DD. */
export function fmtDate(x: Date | number | string): string {
  return toDate(x).toISOString().slice(0, 10)
}

/** 24-hour UTC time: HH:MM, or HH:MM:SS when withSeconds. */
export function fmtTime(x: Date | number | string, withSeconds = false): string {
  return toDate(x).toISOString().slice(11, withSeconds ? 19 : 16)
}

/** YYYY-MM-DD HH:MM in UTC. */
export function fmtDateTime(x: Date | number | string): string {
  const iso = toDate(x).toISOString()
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/format.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Replace locale-based call sites**

In `frontend/src/components/BrCard.tsx`, add `fmtDate` to the `../format` import and replace lines 16-19:

```tsx
  const dateStr = br.battle_at ? fmtDate(br.battle_at) : fmtDate(br.created_at)
```

In `frontend/src/components/FightList.tsx`, import `fmtTime` from `../format` and replace the locale call (line 23-25) so the time renders as:

```tsx
    ? fmtTime(fight.started_at)
```

In `frontend/src/views/FightDetailPage.tsx`, import `fmtDateTime` from `../format` and replace line 236:

```tsx
              {fmtDateTime(fight.started_at)} UTC
```

In `frontend/src/views/CharacterTimelinePage.tsx`, import `fmtTime` from `../format` and replace the body of the time formatter (lines 7-9) with:

```tsx
  return fmtTime(ts, true)
```

In `frontend/src/views/LogsPage.tsx`, import `fmtDateTime` from `../format` and replace line 16-18 body:

```tsx
    return `${fmtDateTime(s)} UTC`
```

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: PASS (no locale-dependent failures; existing tests still green).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/format.ts frontend/src/format.test.ts frontend/src/components/BrCard.tsx frontend/src/components/FightList.tsx frontend/src/views/FightDetailPage.tsx frontend/src/views/CharacterTimelinePage.tsx frontend/src/views/LogsPage.tsx
git commit -m "feat(fe): shared ISO/UTC date-time formatters + sweep locale sites"
```

---

### Task 2: Weapon classifier (pure backend module)

**Files:**
- Create: `app/analytics/weapons.py`
- Create: `tests/test_weapons.py`

**Interfaces:**
- Produces: `classify_weapon(module_name: str | None) -> WeaponClass` where `WeaponClass(category: str, fallback_name: str | None)`. `category ∈ {hybrid, projectile, laser, missile, rocket, torpedo, smartbomb, bomb, other}`. `fallback_name` is a canonical SDE module name used to resolve a family icon when the exact module name doesn't resolve. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weapons.py`:

```python
from app.analytics.weapons import classify_weapon


def test_railgun_is_hybrid_with_fallback():
    w = classify_weapon("250mm Railgun II")
    assert w.category == "hybrid"
    assert w.fallback_name == "250mm Railgun II"


def test_autocannon_is_projectile():
    assert classify_weapon("425mm AutoCannon II").category == "projectile"


def test_pulse_laser_is_laser():
    assert classify_weapon("Mega Pulse Laser II").category == "laser"


def test_rocket_before_missile():
    # 'rocket' must win over the generic 'missile' substring rule.
    assert classify_weapon("Rocket Launcher II").category == "rocket"


def test_heavy_missile_is_missile():
    assert classify_weapon("Heavy Missile Launcher II").category == "missile"


def test_smartbomb_before_bomb():
    assert classify_weapon("Large EMP Smartbomb II").category == "smartbomb"


def test_unknown_module_is_other_no_fallback():
    w = classify_weapon("Some Weird Faction Thing")
    assert w.category == "other"
    assert w.fallback_name is None


def test_none_module_is_other():
    assert classify_weapon(None).category == "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_weapons.py -q`
Expected: FAIL — `ModuleNotFoundError: app.analytics.weapons`.

- [ ] **Step 3: Implement the classifier**

Create `app/analytics/weapons.py`:

```python
"""Pure weapon-name classifier for damage log lines.

Maps an EVE module/weapon name (as it appears in a gamelog damage line) to a
weapon *category* and a canonical *fallback* module name. The caller resolves
the real module's icon by exact name first; when that misses (faction/abyssal
names), it resolves the family fallback name instead. Keyword order matters:
more specific terms (rocket, smartbomb) are checked before their generic
substrings (missile, bomb).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeaponClass:
    category: str
    fallback_name: str | None


# (substring, category, fallback_name) — first match wins; order is significant.
_KEYWORDS: tuple[tuple[str, str, str | None], ...] = (
    ("railgun", "hybrid", "250mm Railgun II"),
    ("blaster", "hybrid", "250mm Railgun II"),
    ("autocannon", "projectile", "425mm AutoCannon II"),
    ("artillery", "projectile", "425mm AutoCannon II"),
    ("pulse laser", "laser", "Mega Pulse Laser II"),
    ("beam laser", "laser", "Mega Pulse Laser II"),
    ("laser", "laser", "Mega Pulse Laser II"),
    ("rocket", "rocket", "Rocket Launcher II"),
    ("torpedo", "torpedo", "Torpedo Launcher II"),
    ("missile", "missile", "Heavy Missile Launcher II"),
    ("smartbomb", "smartbomb", "Large EMP Smartbomb II"),
    ("bomb", "bomb", None),
)


def classify_weapon(module_name: str | None) -> WeaponClass:
    """Classify *module_name* into a weapon family. See module docstring."""
    if not module_name:
        return WeaponClass("other", None)
    low = module_name.lower()
    for kw, category, fallback in _KEYWORDS:
        if kw in low:
            return WeaponClass(category, fallback)
    return WeaponClass("other", None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_weapons.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/analytics/weapons.py tests/test_weapons.py
git commit -m "feat(be): pure weapon-name classifier"
```

---

### Task 3: Contributions payload exposes weapon icon

**Files:**
- Modify: `app/analytics/fleet.py` (`Contribution` dataclass + `fleet_contributions`)
- Modify: `app/api/schemas.py` (`ContributionOut`)
- Modify: `app/api/fleet.py` (router mapping in `get_contributions`)
- Modify: `frontend/src/api.ts` (`Contribution` interface)
- Modify: `tests/test_e3_fleet_timeline.py` (extend contributions test)

**Interfaces:**
- Consumes: `classify_weapon` (Task 2).
- Produces: `Contribution` / `ContributionOut` gain `module_name: str | None`, `icon_type_id: int | None`, `weapon_category: str | None`. Frontend `Contribution` gains the same fields. Consumed by Task 6 (`MomentDetailPanel`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_e3_fleet_timeline.py`:

```python
async def test_contributions_damage_row_has_weapon_icon(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_contributions
    from app.config import get_settings
    from app.db.models import GamelogFile, InventoryType, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        # SDE row so the exact module name resolves to a type_id.
        session.add(InventoryType(type_id=3174, name="250mm Railgun II"))
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="zz", mime="text/plain", size=1,
                         parse_status="parsed", event_count=1,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        ts = BUCKET_TS_1
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=ts,
                             effect_type="damage", direction="out", amount=400.0,
                             other_name="Enemy1", module_name="250mm Railgun II", fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        rows = await fleet_contributions(session, br_id, int(ts.timestamp()), get_settings())

    dmg = next(r for r in rows if r.target_name == "Enemy1")
    assert dmg.module_name == "250mm Railgun II"
    assert dmg.icon_type_id == 3174
    assert dmg.weapon_category == "hybrid"


async def test_contributions_non_damage_row_has_no_weapon(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_contributions
    from app.config import get_settings
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="yy", mime="text/plain", size=1,
                         parse_status="parsed", event_count=1,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        ts = BUCKET_TS_1
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=ts,
                             effect_type="rep_armor", direction="out", amount=500.0,
                             other_name="Friend1", module_name="Large Remote Armor Repairer II",
                             fight_id=fight_id))
        await session.commit()

    async with db_session_maker() as session:
        rows = await fleet_contributions(session, br_id, int(ts.timestamp()), get_settings())

    rep = next(r for r in rows if r.target_name == "Friend1")
    assert rep.module_name is None
    assert rep.icon_type_id is None
    assert rep.weapon_category is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e3_fleet_timeline.py::test_contributions_damage_row_has_weapon_icon -q`
Expected: FAIL — `AttributeError: 'Contribution' object has no attribute 'module_name'`.

- [ ] **Step 3: Extend the `Contribution` dataclass**

In `app/analytics/fleet.py`, replace the `Contribution` dataclass (lines 69-80) with:

```python
@dataclass
class Contribution:
    """One source→target aggregate within a single time bucket."""

    source_character_id: int | None
    source_name: str
    target_name: str
    effect_type: str
    direction: str
    group: str
    value: float
    module_name: str | None = None
    icon_type_id: int | None = None
    weapon_category: str | None = None
```

- [ ] **Step 4: Resolve weapon icons in `fleet_contributions`**

In `app/analytics/fleet.py`, add imports near the top with the existing model imports:

```python
from app.analytics.weapons import classify_weapon
from app.db.models import InventoryType  # add to the existing app.db.models import block
```

Replace the aggregation+build body of `fleet_contributions` (the block from `agg: dict[...]` through the `return out` near line 175) with:

```python
    agg: dict[tuple[int | None, str, str, str], float] = {}
    # Per damage group, track HP per module so we can pick the dominant weapon.
    module_dmg: dict[tuple[int | None, str, str, str], dict[str, float]] = {}
    for cid, other, eff, direction, amount, module in rows:
        key = (cid, _clean_target_name(other), eff or "", direction or "")
        contrib = 1.0 if eff in _COUNT_EFFECTS else abs(amount or 0.0)
        agg[key] = agg.get(key, 0.0) + contrib
        if eff == "damage" and module:
            module_dmg.setdefault(key, {})[module] = (
                module_dmg.setdefault(key, {}).get(module, 0.0) + abs(amount or 0.0)
            )

    # Dominant module per damage group + the family fallback names it may need.
    top_module: dict[tuple[int | None, str, str, str], str] = {
        key: max(mods.items(), key=lambda kv: kv[1])[0] for key, mods in module_dmg.items()
    }
    wanted_names: set[str] = set()
    for name in top_module.values():
        wanted_names.add(name)
        fb = classify_weapon(name).fallback_name
        if fb:
            wanted_names.add(fb)
    name_to_type: dict[str, int] = {}
    if wanted_names:
        for inv in (
            await session.execute(
                select(InventoryType).where(InventoryType.name.in_(wanted_names))
            )
        ).scalars():
            name_to_type[inv.name] = inv.type_id

    names = await _resolve_char_names(session, settings, {k[0] for k in agg if k[0] is not None})

    out: list[Contribution] = []
    for (cid, other, eff, direction), val in agg.items():
        module = top_module.get((cid, other, eff, direction))
        icon_type_id: int | None = None
        category: str | None = None
        if module is not None:
            wc = classify_weapon(module)
            category = wc.category
            icon_type_id = name_to_type.get(module) or (
                name_to_type.get(wc.fallback_name) if wc.fallback_name else None
            )
        out.append(
            Contribution(
                source_character_id=cid,
                source_name=(names.get(cid) or f"Char {cid}") if cid is not None else "?",
                target_name=other,
                effect_type=eff,
                direction=direction,
                group=_EFFECT_GROUP.get(eff, "other"),
                value=val,
                module_name=module,
                icon_type_id=icon_type_id,
                weapon_category=category,
            )
        )
    out.sort(key=lambda c: c.value, reverse=True)
    return out
```

Also add `LogEvent.module_name` to the `select(...)` in `fleet_contributions` (the query near line 138). The select column list becomes:

```python
            select(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.effect_type,
                LogEvent.direction,
                LogEvent.amount,
                LogEvent.module_name,
            ).where(
```

- [ ] **Step 5: Extend the API schema + router**

In `app/api/schemas.py`, replace `ContributionOut` (lines 338-347) with:

```python
class ContributionOut(BaseModel):
    """One source→target aggregate within a clicked time bucket."""

    source_character_id: int | None
    source_name: str
    target_name: str
    effect_type: str
    direction: str
    group: str  # 'damage' | 'cap' | 'ewar'
    value: float
    module_name: str | None = None
    icon_type_id: int | None = None
    weapon_category: str | None = None
```

In `app/api/fleet.py`, update the `ContributionOut(...)` construction inside `get_contributions` to pass the three new fields:

```python
            ContributionOut(
                source_character_id=c.source_character_id,
                source_name=c.source_name,
                target_name=c.target_name,
                effect_type=c.effect_type,
                direction=c.direction,
                group=c.group,
                value=c.value,
                module_name=c.module_name,
                icon_type_id=c.icon_type_id,
                weapon_category=c.weapon_category,
            )
```

- [ ] **Step 6: Update the frontend type**

In `frontend/src/api.ts`, replace the `Contribution` interface (lines 331-339) with:

```ts
export interface Contribution {
  source_character_id: number | null
  source_name: string
  target_name: string
  effect_type: string
  direction: string
  group: string // 'damage' | 'cap' | 'ewar'
  value: number
  module_name: string | null
  icon_type_id: number | null
  weapon_category: string | null
}
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_e3_fleet_timeline.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS (including the two new tests); tsc no errors.

- [ ] **Step 8: Commit**

```bash
git add app/analytics/fleet.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_e3_fleet_timeline.py
git commit -m "feat: contributions expose resolved weapon icon (module/type_id/category)"
```

---

### Task 4: Victim pilot name on kill markers

**Files:**
- Modify: `app/analytics/fleet.py` (`KillEvent` dataclass + `fleet_timeline` resolution)
- Modify: `app/api/schemas.py` (`KillEventOut`)
- Modify: `app/api/fleet.py` (kills mapping in `get_fleet_timeline`)
- Modify: `frontend/src/api.ts` (`KillEvent` interface)
- Modify: `tests/test_e3_fleet_timeline.py` (extend kills test)

**Interfaces:**
- Produces: `KillEvent` / `KillEventOut` / frontend `KillEvent` gain `victim_character_name: str | null`. Consumed by Task 6 (kill tooltip).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_e3_fleet_timeline.py`:

```python
async def test_kill_has_victim_character_name(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.db.models import Character

    km_time = FIGHT_START + dt.timedelta(seconds=60)
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        session.add(Character(character_id=CHAR_B, name="Mara Sant"))
        await _insert_killmail(session, fight_id=fight_id, side_idx=1, victim_char_id=CHAR_B,
                               ship_type_id=_SHIP_TYPE_ID, total_value=1.0, killmail_time=km_time)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    k = next(k for k in tl.kills if k.victim_character_id == CHAR_B)
    assert k.victim_character_name == "Mara Sant"


async def test_kill_unknown_victim_name_is_none(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline

    km_time = FIGHT_START + dt.timedelta(seconds=90)
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_killmail(session, fight_id=fight_id, side_idx=1, victim_char_id=777000777,
                               ship_type_id=_SHIP_TYPE_ID, total_value=1.0, killmail_time=km_time)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id)

    k = next(k for k in tl.kills if k.victim_character_id == 777000777)
    assert k.victim_character_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e3_fleet_timeline.py::test_kill_has_victim_character_name -q`
Expected: FAIL — `AttributeError: 'KillEvent' object has no attribute 'victim_character_name'`.

- [ ] **Step 3: Extend the `KillEvent` dataclass**

In `app/analytics/fleet.py`, in the `KillEvent` dataclass (lines 272-286) add the field after `victim_character_id`:

```python
    victim_character_id: int | None
    victim_character_name: str | None
    victim_ship_name: str
```

- [ ] **Step 4: Resolve victim names in `fleet_timeline`**

In `app/analytics/fleet.py`, inside `fleet_timeline`, after `km_map` is built (right after the `km_map: dict[int, Killmail] = {...}` line ~430) add a victim-name lookup:

```python
        victim_ids = {
            km.victim_character_id for km in km_map.values() if km.victim_character_id is not None
        }
        victim_names: dict[int, str] = {}
        if victim_ids:
            for ch in (
                await session.execute(
                    select(Character).where(Character.character_id.in_(victim_ids))
                )
            ).scalars():
                if ch.name:
                    victim_names[ch.character_id] = ch.name
```

Then in the `kills.append(KillEvent(...))` call, add the name field:

```python
                KillEvent(
                    ts=_epoch(km.killmail_time),
                    killmail_id=km_id,
                    victim_character_id=km.victim_character_id,
                    victim_character_name=(
                        victim_names.get(km.victim_character_id)
                        if km.victim_character_id is not None
                        else None
                    ),
                    victim_ship_name=ship_name,
                    victim_ship_type_id=km.victim_ship_type_id,
                    side_kind=side_kind,
                    isk=km.total_value,
                )
```

(`Character` is already imported in `app/analytics/fleet.py`.)

- [ ] **Step 5: Extend schema + router**

In `app/api/schemas.py`, in `KillEventOut` (lines 326-335) add the field after `victim_character_id`:

```python
    victim_character_id: int | None
    victim_character_name: str | None
    victim_ship_name: str
```

In `app/api/fleet.py`, in the `KillEventOut(...)` construction inside `get_fleet_timeline`, add:

```python
                victim_character_id=k.victim_character_id,
                victim_character_name=k.victim_character_name,
                victim_ship_name=k.victim_ship_name,
```

- [ ] **Step 6: Update the frontend type**

In `frontend/src/api.ts`, in the `KillEvent` interface (lines 311-319) add after `victim_character_id`:

```ts
  victim_character_id: number | null
  victim_character_name: string | null
  victim_ship_name: string
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_e3_fleet_timeline.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS; tsc fails only inside `FleetSection.test.tsx` fixtures missing the new field — fix those fixtures by adding `victim_character_name: 'Tengu Pilot'` / `victim_character_name: null` to the two `kills` entries (lines 52-53), then re-run. Expected after fix: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/analytics/fleet.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_e3_fleet_timeline.py frontend/src/components/FleetSection.test.tsx
git commit -m "feat: kill events carry victim character name"
```

---

### Task 5: Fleet composition analytics + endpoint

**Files:**
- Create: `app/analytics/composition.py`
- Modify: `app/api/schemas.py` (composition schemas)
- Modify: `app/api/fleet.py` (new `GET /api/brs/{br_id}/composition` endpoint)
- Modify: `frontend/src/api.ts` (composition types + `api.composition`)
- Create: `tests/test_composition.py`

**Interfaces:**
- Consumes: `classify_entity`, `load_overrides`, `_resolve_char_names` (from `app.analytics.fleet`).
- Produces: `fleet_composition(session, br_id, *, baseline_alliances, baseline_corps, overrides, settings, char_to_user) -> CompositionResult`. Endpoint returns `CompositionOut { by_user_available: bool, sides: [...] }`. Frontend `api.composition(brId) -> CompositionOut`. Consumed by Task 7 (`FleetsPanel`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_composition.py`:

```python
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, update

from app.db.models import (
    BattleReport,
    BrFight,
    FightKill,
    InventoryType,
    KillmailAttacker,
)
from tests.test_association import _insert_fight

FIGHT_START = dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC)
VICTIM = 9001
ATTACKER = 9002
ABSOLUTION = 22428


async def _seed(session):  # type: ignore[no-untyped-def]
    # _insert_fight creates a Fight + Killmail (victim VICTIM, ship type 1 "TestShip")
    # + one KillmailAttacker (attacker_idx=0, character ATTACKER, NO ship) + Characters
    # named f"Char{id}".
    fight_id = await _insert_fight(session, victim_char_id=VICTIM, attacker_char_id=ATTACKER,
                                   started_at=FIGHT_START, ended_at=FIGHT_START)
    km_id = (
        await session.execute(select(FightKill.killmail_id).where(FightKill.fight_id == fight_id))
    ).scalar_one()
    # Give the existing attacker a real ship so composition can count it.
    await session.execute(
        update(KillmailAttacker)
        .where(KillmailAttacker.killmail_id == km_id, KillmailAttacker.character_id == ATTACKER)
        .values(ship_type_id=ABSOLUTION)
    )
    session.add(InventoryType(type_id=ABSOLUTION, name="Absolution"))
    br_id = str(uuid.uuid4())
    session.add(BattleReport(br_id=br_id, source="demo", source_url="http://x", source_ref="r",
                             created_by_user="t", status="ready", progress_pct=100,
                             created_at=dt.datetime.now(dt.UTC)))
    session.add(BrFight(br_id=br_id, fight_id=fight_id, seq=0))
    await session.flush()
    return br_id, fight_id


@pytest.mark.asyncio
async def test_composition_counts_ships_per_side(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    # The attacker flies an Absolution; everyone is unassigned (no baseline/override).
    side = next(s for s in result.sides if any(p.character_id == ATTACKER for p in s.pilots))
    ship = next(sh for sh in side.ships if sh.ship_type_id == ABSOLUTION)
    assert ship.count == 1
    assert ship.ship_name == "Absolution"
    pilot = next(p for p in side.pilots if p.character_id == ATTACKER)
    assert pilot.character_name == "Char9002"
    assert pilot.lost is False
    assert pilot.user_name is None  # char_to_user not provided


@pytest.mark.asyncio
async def test_composition_attaches_user_when_provided(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings

    async with db_session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user={ATTACKER: "hfrench"},
        )

    pilot = next(p for s in result.sides for p in s.pilots if p.character_id == ATTACKER)
    assert pilot.user_name == "hfrench"


@pytest.mark.asyncio
async def test_api_composition_contract(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.config import get_app_config, get_settings
    from app.db.engine import get_sessionmaker, init_models, reset_engine_for_tests
    from app.main import create_app
    from tests.conftest import CREATOR_HEADERS, MEMBER_HEADERS, TEST_TOKEN

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("DATA_SOURCE", "demo")
    monkeypatch.setenv("NV_TOKEN", TEST_TOKEN)
    get_settings.cache_clear(); get_app_config.cache_clear(); reset_engine_for_tests()
    settings = get_settings()
    await init_models(settings)
    session_maker = get_sessionmaker(settings)
    async with session_maker() as session:
        br_id, _ = await _seed(session)
        await session.commit()
    get_app_config.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        member = client.get(f"/api/brs/{br_id}/composition", headers=MEMBER_HEADERS)
        creator = client.get(f"/api/brs/{br_id}/composition", headers=CREATOR_HEADERS)

    assert member.status_code == 200
    assert member.json()["by_user_available"] is False
    assert all(p["user_name"] is None for s in member.json()["sides"] for p in s["pilots"])
    assert creator.status_code == 200

    reset_engine_for_tests(); get_settings.cache_clear(); get_app_config.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py -q`
Expected: FAIL — `ModuleNotFoundError: app.analytics.composition`.

- [ ] **Step 3: Implement the schemas**

Append to `app/api/schemas.py`:

```python
# ---------------------------------------------------------------------------
# Fleet composition schemas
# ---------------------------------------------------------------------------


class CompositionShipOut(BaseModel):
    ship_type_id: int
    ship_name: str
    count: int


class CompositionPilotOut(BaseModel):
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    user_name: str | None = None


class CompositionSideOut(BaseModel):
    side_kind: str  # 'friendly' | 'hostile' | 'unassigned'
    pilot_count: int
    ships: list[CompositionShipOut]
    pilots: list[CompositionPilotOut]


class CompositionOut(BaseModel):
    by_user_available: bool
    sides: list[CompositionSideOut]
```

- [ ] **Step 4: Implement the analytics**

Create `app/analytics/composition.py`:

```python
"""Fleet composition: per-side ship counts, per-pilot roster, and (for elevated
callers) char→user grouping. Killmail-derived: a pilot is any character seen as
a Killmail victim or a KillmailAttacker across the BR's fights.

Each pilot maps to exactly one ship and one side:
  - ship: the victim ship if the pilot died, else their most-frequent attacker ship.
  - side: classify_entity(alliance_id, corp_id, baseline, overrides).
Consistent with the kill-marker classification on the fleet timeline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fleet import _resolve_char_names
from app.analytics.sides_config import EntityKey, classify_entity
from app.config import Settings
from app.db.models import (
    BrFight,
    FightKill,
    InventoryType,
    Killmail,
    KillmailAttacker,
)

_SIDE_ORDER = ("friendly", "hostile", "unassigned")


@dataclass
class CompositionPilot:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    user_name: str | None


@dataclass
class CompositionShip:
    ship_type_id: int
    ship_name: str
    count: int


@dataclass
class CompositionSide:
    side_kind: str
    pilot_count: int
    ships: list[CompositionShip]
    pilots: list[CompositionPilot]


@dataclass
class CompositionResult:
    sides: list[CompositionSide]


@dataclass
class _Pilot:
    side: str
    ship_type_id: int | None
    lost: bool
    attacker_ships: Counter  # ship_type_id -> occurrences (for non-victims)


async def fleet_composition(
    session: AsyncSession,
    br_id: str,
    *,
    baseline_alliances: set[int],
    baseline_corps: set[int],
    overrides: dict[EntityKey, str],
    settings: Settings,
    char_to_user: dict[int, str] | None,
) -> CompositionResult:
    """Build per-side composition for *br_id*. See module docstring."""
    km_ids = list(
        (
            await session.execute(
                select(FightKill.killmail_id)
                .join(BrFight, BrFight.fight_id == FightKill.fight_id)
                .where(BrFight.br_id == br_id)
            )
        ).scalars()
    )
    if not km_ids:
        return CompositionResult(sides=[])

    def _side(alli: int | None, corp: int | None) -> str:
        return classify_entity(
            alli, corp, baseline_alliances=baseline_alliances,
            baseline_corps=baseline_corps, overrides=overrides,
        )

    pilots: dict[int, _Pilot] = {}

    # Victims first — authoritative ship + side, lost=True.
    for char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                Killmail.victim_character_id,
                Killmail.victim_ship_type_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        pilots[char_id] = _Pilot(side=_side(alli, corp), ship_type_id=ship_id,
                                 lost=True, attacker_ships=Counter())

    # Attackers — fill in pilots who didn't die; accumulate candidate ships.
    for char_id, ship_id, alli, corp in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                KillmailAttacker.ship_type_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        p = pilots.get(char_id)
        if p is None:
            p = _Pilot(side=_side(alli, corp), ship_type_id=None, lost=False,
                       attacker_ships=Counter())
            pilots[char_id] = p
        if not p.lost and ship_id is not None:
            p.attacker_ships[ship_id] += 1

    # Resolve each non-victim pilot's ship to its most common attacker ship.
    for p in pilots.values():
        if not p.lost and p.ship_type_id is None and p.attacker_ships:
            p.ship_type_id = p.attacker_ships.most_common(1)[0][0]

    # Resolve names.
    char_names = await _resolve_char_names(session, settings, set(pilots))
    ship_ids = {p.ship_type_id for p in pilots.values() if p.ship_type_id is not None}
    ship_names: dict[int, str] = {}
    if ship_ids:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.type_id.in_(ship_ids)))
        ).scalars():
            ship_names[inv.type_id] = inv.name

    # Group into sides.
    by_side: dict[str, list[CompositionPilot]] = {}
    for char_id, p in pilots.items():
        by_side.setdefault(p.side, []).append(
            CompositionPilot(
                character_id=char_id,
                character_name=char_names.get(char_id) or f"Char {char_id}",
                ship_type_id=p.ship_type_id,
                ship_name=(ship_names.get(p.ship_type_id, "Unknown")
                           if p.ship_type_id is not None else "Unknown"),
                lost=p.lost,
                user_name=(char_to_user.get(char_id) if char_to_user else None),
            )
        )

    sides: list[CompositionSide] = []
    for side_kind in _SIDE_ORDER:
        plist = by_side.get(side_kind)
        if not plist:
            continue
        plist.sort(key=lambda x: (x.ship_name, x.character_name))
        counts: Counter = Counter()
        for pilot in plist:
            if pilot.ship_type_id is not None:
                counts[pilot.ship_type_id] += 1
        ships = [
            CompositionShip(ship_type_id=sid, ship_name=ship_names.get(sid, "Unknown"), count=c)
            for sid, c in counts.most_common()
        ]
        sides.append(CompositionSide(side_kind=side_kind, pilot_count=len(plist),
                                     ships=ships, pilots=plist))
    return CompositionResult(sides=sides)
```

- [ ] **Step 5: Add the endpoint**

In `app/api/fleet.py`, add imports at the top:

```python
from fastapi import APIRouter, HTTPException, Request

from app.analytics.composition import fleet_composition
from app.api.access import acting_user
from app.api.auth import can_create_br
from app.api.schemas import (
    CompositionOut,
    CompositionPilotOut,
    CompositionShipOut,
    CompositionSideOut,
    # ...keep existing imports...
)
from app.roster.snapshot import get_roster_store
```

Append the endpoint to `app/api/fleet.py`:

```python
@router.get("/api/brs/{br_id}/composition")
async def get_composition(
    br_id: str, request: Request, session: SessionDep
) -> CompositionOut:
    """Per-side fleet composition. Elevated callers (FC/HC) also get char→user."""
    await _require_br(br_id, session)
    cfg = get_app_config()
    settings = get_settings()
    acting = await acting_user(request, settings)
    char_to_user: dict[int, str] | None = None
    by_user_available = False
    if can_create_br(acting):
        try:
            roster = await get_roster_store(settings).get()
            char_to_user = dict(roster.char_to_user)
            by_user_available = True
        except Exception:  # roster unavailable → no user grouping
            char_to_user = None
            by_user_available = False
    overrides = await load_overrides(session, br_id)
    result = await fleet_composition(
        session, br_id,
        baseline_alliances=set(cfg.our_alliance_ids),
        baseline_corps=set(cfg.our_corp_ids),
        overrides=overrides, settings=settings, char_to_user=char_to_user,
    )
    return CompositionOut(
        by_user_available=by_user_available,
        sides=[
            CompositionSideOut(
                side_kind=s.side_kind,
                pilot_count=s.pilot_count,
                ships=[CompositionShipOut(ship_type_id=sh.ship_type_id,
                                          ship_name=sh.ship_name, count=sh.count) for sh in s.ships],
                pilots=[CompositionPilotOut(character_id=p.character_id,
                                            character_name=p.character_name,
                                            ship_type_id=p.ship_type_id, ship_name=p.ship_name,
                                            lost=p.lost, user_name=p.user_name) for p in s.pilots],
            )
            for s in result.sides
        ],
    )
```

- [ ] **Step 6: Add frontend types + client method**

In `frontend/src/api.ts`, add after the contributions types:

```ts
export interface CompositionShip {
  ship_type_id: number
  ship_name: string
  count: number
}

export interface CompositionPilot {
  character_id: number
  character_name: string
  ship_type_id: number | null
  ship_name: string
  lost: boolean
  user_name: string | null
}

export interface CompositionSide {
  side_kind: string // 'friendly' | 'hostile' | 'unassigned'
  pilot_count: number
  ships: CompositionShip[]
  pilots: CompositionPilot[]
}

export interface CompositionResponse {
  by_user_available: boolean
  sides: CompositionSide[]
}
```

Add to the `api` object (next to `fleetTimeline`):

```ts
  composition: (brId: string) =>
    jsonFetch<CompositionResponse>(`${API}/brs/${brId}/composition`),
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_composition.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS (counts, user attach, member-hides-users contract); tsc clean.

- [ ] **Step 8: Commit**

```bash
git add app/analytics/composition.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_composition.py
git commit -m "feat: fleet composition analytics + endpoint (ships/pilots/by-user)"
```

---

### Task 6: Split FleetSection → FleetGraph + MomentDetailPanel

**Files:**
- Create: `frontend/src/components/FleetGraph.tsx`
- Create: `frontend/src/components/MomentDetailPanel.tsx`
- Create: `frontend/src/components/MomentDetailPanel.test.tsx`
- Modify: `frontend/src/components/FleetSection.tsx` (becomes a thin composition of the two)
- Modify: `frontend/src/components/FleetSection.test.tsx` (still green against the composed wrapper)
- Modify: `frontend/src/styles/app.css` (no new rules required here; reuse existing `.fleet-*`, `.contrib-*`, `.focus-*`, `.kill-tip*`)

**Interfaces:**
- Produces:
  - `FleetGraph({ brId, reloadKey?, selectedTs, onSelectTs })` — renders controls + the three uPlot panels + kill legend. `selectedTs: number | null`, `onSelectTs: (ts: number | null) => void`. Renders the `fleet-chart-area` test id. Kill tooltip shows `victim_character_name`.
  - `MomentDetailPanel({ brId, at })` — `at: number | null`; self-fetches `api.contributions`, renders target cards with weapon icons, or a hint when `at` is null.
- Consumed by Task 8 (page wires both; lifts `selectedTs`).

This task is a **refactor by extraction**. Move the existing code blocks; change only the wiring and the two new behaviours (weapon icon, victim name).

- [ ] **Step 1: Write the failing test for MomentDetailPanel**

Create `frontend/src/components/MomentDetailPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse } from '../api'
import { MomentDetailPanel } from './MomentDetailPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, contributions: vi.fn() } }
})
import { api } from '../api'

const resp: ContributionsResponse = {
  at: 1000,
  bucket_seconds: 5,
  rows: [
    { source_character_id: 1, source_name: 'Talun', target_name: 'Loki', effect_type: 'damage',
      direction: 'out', group: 'damage', value: 9200, module_name: '250mm Railgun II',
      icon_type_id: 3174, weapon_category: 'hybrid' },
    { source_character_id: 2, source_name: 'Aiden', target_name: 'Nestor', effect_type: 'rep_armor',
      direction: 'in', group: 'damage', value: 8000, module_name: null, icon_type_id: null,
      weapon_category: null },
  ],
}

describe('MomentDetailPanel', () => {
  beforeEach(() => vi.mocked(api.contributions).mockReset())

  it('shows a hint when no moment is selected', () => {
    render(<MomentDetailPanel brId="br1" at={null} />)
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
    expect(api.contributions).not.toHaveBeenCalled()
  })

  it('renders a weapon icon for damage rows and effect icon for non-damage', async () => {
    vi.mocked(api.contributions).mockResolvedValue(resp)
    render(<MomentDetailPanel brId="br1" at={1000} />)
    await waitFor(() => expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument())
    const weapon = screen.getByTitle('250mm Railgun II') as HTMLImageElement
    expect(weapon.src).toContain('/types/3174/icon')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/MomentDetailPanel.test.tsx`
Expected: FAIL — cannot resolve `./MomentDetailPanel`.

- [ ] **Step 3: Create MomentDetailPanel.tsx**

Move these symbols **out of** `FleetSection.tsx` and **into** `MomentDetailPanel.tsx`: `EFFECT_ICON`, `EFFECT_LABEL`, `EffectIcon`, `Row` type, `TargetGroup`, `groupByTarget`, `TargetCard`, `GROUP_TOTALS`, `ContributionsPanel`, plus the `fmtCompact` helper (copy it; it is also used by FleetGraph — keep a copy in each file or export it from a shared module — for this task, keep a local copy in each). Then wrap them as a self-fetching panel. Full new file:

```tsx
// Moment detail: the source→target breakdown for one clicked 5s bucket.
import { useEffect, useState } from 'react'
import type { Contribution, ContributionsResponse } from '../api'
import { api } from '../api'
import { fmtTime } from '../format'

function fmtCompact(n: number): string {
  const a = Math.abs(n)
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return `${Math.round(n)}`
}

const EFFECT_ICON: Record<string, number> = {
  damage: 485, rep_armor: 11355, rep_shield: 3586, neut: 533, nos: 530,
  cap_transfer: 529, scram: 447, disrupt: 3242, jam: 1957,
}
const EFFECT_LABEL: Record<string, string> = {
  damage: 'damage', rep_armor: 'armor rep', rep_shield: 'shield rep', neut: 'neut',
  nos: 'nos', cap_transfer: 'cap', scram: 'scram', disrupt: 'point', jam: 'jam',
}

function RowIcon({ row }: { row: Contribution }) {
  // Damage rows with a resolved weapon → the weapon's own EVE icon.
  if (row.effect_type === 'damage' && row.icon_type_id != null) {
    return (
      <img
        className="contrib-eff-icon"
        src={`https://images.evetech.net/types/${row.icon_type_id}/icon?size=32`}
        alt={row.module_name ?? 'weapon'}
        title={row.module_name ?? undefined}
        width={18}
        height={18}
      />
    )
  }
  const id = EFFECT_ICON[row.effect_type]
  if (id == null) return <span className="contrib-eff-dot" />
  return (
    <img
      className="contrib-eff-icon"
      src={`https://images.evetech.net/types/${id}/icon?size=32`}
      alt={EFFECT_LABEL[row.effect_type] ?? row.effect_type}
      title={row.module_name ?? EFFECT_LABEL[row.effect_type] ?? row.effect_type}
      width={18}
      height={18}
    />
  )
}

interface TargetGroup { target: string; total: number; rows: Contribution[] }

function groupByTarget(rows: Contribution[]): TargetGroup[] {
  const map = new Map<string, TargetGroup>()
  for (const r of rows) {
    let g = map.get(r.target_name)
    if (!g) { g = { target: r.target_name, total: 0, rows: [] }; map.set(r.target_name, g) }
    g.total += r.value
    g.rows.push(r)
  }
  const groups = [...map.values()]
  for (const g of groups) g.rows.sort((a, b) => b.value - a.value)
  groups.sort((a, b) => b.total - a.total)
  return groups
}

function TargetCard({ group }: { group: TargetGroup }) {
  return (
    <div className="focus-card">
      <div className="focus-card-head" title={group.target}>{group.target}</div>
      {group.rows.slice(0, 12).map((r, i) => (
        <div className="focus-row" key={i}>
          <RowIcon row={r} />
          <span className="focus-src" title={r.source_name}>{r.source_name}</span>
          <span className="dim focus-dir">{r.direction === 'in' ? '←' : '→'}</span>
          <span className="focus-val">{fmtCompact(r.value)}</span>
        </div>
      ))}
      {group.rows.length > 12 && (
        <div className="dim" style={{ fontSize: '0.7rem' }}>+{group.rows.length - 12} more…</div>
      )}
    </div>
  )
}

const GROUP_TOTALS = [
  { id: 'damage', label: 'Dmg/Rep' },
  { id: 'cap', label: 'Cap' },
  { id: 'ewar', label: 'EWAR' },
]

export function MomentDetailPanel({ brId, at }: { brId: string; at: number | null }) {
  const [data, setData] = useState<ContributionsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (at == null) { setData(null); return }
    setLoading(true); setError(null)
    let cancelled = false
    const handle = setTimeout(() => {
      api.contributions(brId, Math.round(at)).then(
        (d) => { if (!cancelled) { setData(d); setLoading(false) } },
        (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
      )
    }, 120)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [brId, at])

  if (at == null) {
    return (
      <div className="contrib-panel" data-testid="moment-detail-empty">
        <p className="dim" style={{ fontSize: '0.8rem', textAlign: 'center', padding: '1rem 0' }}>
          Click a moment on any graph to break down who applied what.
        </p>
      </div>
    )
  }

  const rows = data?.rows ?? []
  const targets = groupByTarget(rows)
  return (
    <div className="contrib-panel" data-testid="fleet-contrib">
      <div className="contrib-head">
        <strong>{fmtTime(at, true)} UTC <span className="dim">· 5s window</span></strong>
      </div>
      <div className="focus-totals">
        {GROUP_TOTALS.map((g) => {
          const sum = rows.filter((r) => r.group === g.id).reduce((a, r) => a + r.value, 0)
          return (
            <span key={g.id} className="focus-total">
              <span className="dim">{g.label}</span> {fmtCompact(sum)}
            </span>
          )
        })}
      </div>
      {loading && rows.length === 0 && <p className="dim">Loading…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && targets.length === 0 && (
        <p className="dim" style={{ fontSize: '0.78rem' }}>No logged activity in this window.</p>
      )}
      <div className="focus-list">
        {targets.map((g) => <TargetCard key={g.target} group={g} />)}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the MomentDetailPanel test**

Run: `cd frontend && npx vitest run src/components/MomentDetailPanel.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Create FleetGraph.tsx by extraction**

Create `frontend/src/components/FleetGraph.tsx` containing everything currently in `FleetSection.tsx` **except** the moment-detail symbols moved in Step 3. Concretely, move into `FleetGraph.tsx`: the colour constants, `killColor`, `hexToRgba`, `fmtCompact` (local copy), all plugin functions (`zeroBaselinePlugin`, `killMarkersPlugin`, `fightEdgesPlugin`, `sliderPlugin`), `PanelChart`, `ToggleLegend`, `KillLegend`, and a new top-level `FleetGraph` component. Change three things relative to the old `FleetSection`:

1. **Props** — `FleetGraph` takes `{ brId, reloadKey?, selectedTs, onSelectTs }` and uses `selectedTs` / `onSelectTs` in place of the internal `sliderTime` state. Keep `sliderTimeRef`, `positionersRef`, and the debounced contributions fetch **out** — those move to the page/MomentDetailPanel. `handleSliderChange` becomes:

```tsx
  const handleSliderChange = useCallback((ts: number) => {
    sliderTimeRef.current = ts
    positionersRef.current.forEach((fn) => fn())
    onSelectTs(ts)
  }, [onSelectTs])
```

Keep `sliderTimeRef` and `positionersRef` in `FleetGraph` (they drive the on-canvas slider line). Seed `sliderTimeRef.current` from `selectedTs` in an effect so the line reflects external clears:

```tsx
  useEffect(() => {
    sliderTimeRef.current = selectedTs
    positionersRef.current.forEach((fn) => fn())
  }, [selectedTs])
```

Remove the `contrib`, `contribLoading`, `contribError` state and the `closeSlider`/contributions `useEffect` entirely (now MomentDetailPanel's job). Remove the `<div className="fleet-side">…</div>` block. The returned JSX is just `<div className="fleet-main">…</div>` contents (the controls, panels, legend, tip) — drop the outer `fleet-layout`/`fleet-main`/`fleet-side` wrappers; render the controls + panels + `KillLegend` directly under `<div data-testid="fleet-chart-area">`.

2. **Kill tooltip victim name** — in `killMarkersPlugin`'s `showTip`, add the pilot line. Replace the `tip.innerHTML = …` assignment with:

```tsx
    const t = new Date(k.ts * 1000).toISOString().slice(11, 19)
    const isk = k.isk != null ? ` · ${fmtIsk(k.isk)}` : ''
    const pilot = k.victim_character_name
      ? `<div class="kill-tip-pilot">${k.victim_character_name}</div>`
      : ''
    tip.innerHTML =
      `${icon}<div class="kill-tip-text"><div class="kill-tip-ship">${k.victim_ship_name}</div>` +
      pilot +
      `<div class="kill-tip-meta">${t} UTC${isk}</div></div>`
```

3. **No internal smoothing/kill-toggle changes** — keep those controls as they are.

Add a CSS rule to `frontend/src/styles/app.css` next to `.kill-tip-ship`:

```css
.kill-tip-pilot { color: var(--accent); font-size: 0.8rem; }
```

- [ ] **Step 6: Reduce FleetSection.tsx to a thin wrapper**

Replace the entire body of `frontend/src/components/FleetSection.tsx` with a wrapper that owns `selectedTs` and composes the two new components, preserving current single-component behaviour (graph + side panel appears on click):

```tsx
import { useState } from 'react'
import { FleetGraph } from './FleetGraph'
import { MomentDetailPanel } from './MomentDetailPanel'

interface Props {
  brId: string
  reloadKey?: number
}

export function FleetSection({ brId, reloadKey }: Props) {
  const [selectedTs, setSelectedTs] = useState<number | null>(null)
  return (
    <div className="fleet-layout">
      <div className="fleet-main">
        <FleetGraph brId={brId} reloadKey={reloadKey} selectedTs={selectedTs} onSelectTs={setSelectedTs} />
      </div>
      {selectedTs != null && (
        <div className="fleet-side">
          <MomentDetailPanel brId={brId} at={selectedTs} />
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 7: Update FleetSection.test.tsx**

The existing tests assert graph behaviour (panels, toggles, kill legend) and the `fleet-chart-area` test id — all still rendered by `FleetGraph`. Keep the file as-is except: it already mocks `uplot` and `api.fleetTimeline`; no contributions assertions exist there, so no change is needed beyond the `victim_character_name` fixture fields added in Task 4. Run it to confirm.

- [ ] **Step 8: Run the frontend suite + typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS — `FleetSection`, `FleetGraph` (via FleetSection tests), and `MomentDetailPanel` all green.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/FleetGraph.tsx frontend/src/components/MomentDetailPanel.tsx frontend/src/components/MomentDetailPanel.test.tsx frontend/src/components/FleetSection.tsx frontend/src/components/FleetSection.test.tsx frontend/src/styles/app.css
git commit -m "refactor(fe): split FleetSection into FleetGraph + MomentDetailPanel; weapon icons + victim name"
```

---

### Task 7: FleetsPanel (composition / per-character / by-user)

**Files:**
- Create: `frontend/src/components/FleetsPanel.tsx`
- Create: `frontend/src/components/FleetsPanel.test.tsx`
- Modify: `frontend/src/styles/app.css` (fleets/segmented-control rules)

**Interfaces:**
- Consumes: `api.composition` + composition types (Task 5).
- Produces: `FleetsPanel({ brId, reloadKey? })`. Consumed by Task 8.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/FleetsPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompositionResponse } from '../api'
import { FleetsPanel } from './FleetsPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, composition: vi.fn() } }
})
import { api } from '../api'

const base: CompositionResponse = {
  by_user_available: false,
  sides: [
    { side_kind: 'friendly', pilot_count: 2, ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 2 }],
      pilots: [
        { character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution', lost: false, user_name: null },
        { character_id: 2, character_name: 'B', ship_type_id: 22428, ship_name: 'Absolution', lost: true, user_name: null },
      ] },
  ],
}

describe('FleetsPanel', () => {
  beforeEach(() => vi.mocked(api.composition).mockReset())

  it('renders composition counts by default', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByText(/Absolution/)).toBeInTheDocument())
    expect(screen.getByText(/2×/)).toBeInTheDocument()
  })

  it('hides the By-user tab when not available', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Composition/i })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /By user/i })).not.toBeInTheDocument()
  })

  it('shows the By-user tab when available', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      ...base, by_user_available: true,
      sides: [{ ...base.sides[0],
        pilots: base.sides[0].pilots.map((p) => ({ ...p, user_name: 'hfrench' })) }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By user/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By user/i }))
    expect(screen.getByText(/hfrench/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx`
Expected: FAIL — cannot resolve `./FleetsPanel`.

- [ ] **Step 3: Implement FleetsPanel.tsx**

```tsx
// Fleet composition summary with Composition / Per-character / By-user modes.
import { useEffect, useMemo, useState } from 'react'
import type { CompositionPilot, CompositionResponse, CompositionSide } from '../api'
import { api } from '../api'

type Mode = 'composition' | 'character' | 'user'

function shipIcon(id: number | null, size = 30) {
  if (id == null) return <span className="comp-ship-icon comp-ship-none" />
  return (
    <img className="comp-ship-icon" width={size} height={size}
      src={`https://images.evetech.net/types/${id}/icon?size=32`} alt="" />
  )
}

function SideHeader({ side }: { side: CompositionSide }) {
  const hulls = side.ships.length
  const cls = side.side_kind === 'friendly' ? 'friendly' : side.side_kind === 'hostile' ? 'hostile' : ''
  return (
    <div className={`comp-side-h ${cls}`}>
      <span className={`comp-side-name ${cls}`}>{side.side_kind}</span>
      <span className="dim" style={{ fontSize: '0.74rem' }}>{side.pilot_count} pilots · {hulls} hulls</span>
    </div>
  )
}

function CompositionView({ side }: { side: CompositionSide }) {
  return (
    <div>
      <SideHeader side={side} />
      {side.ships.map((sh) => (
        <div className="comp-row" key={sh.ship_type_id}>
          {shipIcon(sh.ship_type_id)}
          <span className="comp-count">{sh.count}×</span>
          <span className="comp-name" title={sh.ship_name}>{sh.ship_name}</span>
        </div>
      ))}
    </div>
  )
}

function PilotRow({ p }: { p: CompositionPilot }) {
  return (
    <div className="comp-row">
      {shipIcon(p.ship_type_id, 18)}
      <span className="comp-name" title={p.character_name}>
        {p.character_name}{p.lost && <span className="comp-lost" title="lost ship"> ✗</span>}
      </span>
      <span className="dim comp-ship-sub">{p.ship_name}</span>
    </div>
  )
}

function CharacterView({ side }: { side: CompositionSide }) {
  return (
    <div>
      <SideHeader side={side} />
      {side.pilots.map((p) => <PilotRow key={p.character_id} p={p} />)}
    </div>
  )
}

function UserView({ side }: { side: CompositionSide }) {
  const groups = useMemo(() => {
    const m = new Map<string, CompositionPilot[]>()
    for (const p of side.pilots) {
      const key = p.user_name ?? 'Unmatched'
      if (!m.has(key)) m.set(key, [])
      m.get(key)!.push(p)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [side.pilots])
  return (
    <div>
      <SideHeader side={side} />
      {groups.map(([user, pilots]) => (
        <div key={user} className="comp-user-group">
          <div className="comp-user-head">▸ {user}</div>
          {pilots.map((p) => <PilotRow key={p.character_id} p={p} />)}
        </div>
      ))}
    </div>
  )
}

export function FleetsPanel({ brId, reloadKey }: { brId: string; reloadKey?: number }) {
  const [data, setData] = useState<CompositionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('composition')

  useEffect(() => {
    let cancelled = false
    setError(null)
    api.composition(brId).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey])

  // If By-user becomes unavailable while selected, fall back to composition.
  useEffect(() => {
    if (mode === 'user' && data && !data.by_user_available) setMode('composition')
  }, [mode, data])

  if (error) return <p className="error-text" data-testid="fleets-error">{error}</p>
  if (!data) return <p className="dim">Loading fleets…</p>
  if (data.sides.length === 0) return <p className="dim" data-testid="fleets-empty">No fleet data.</p>

  return (
    <div data-testid="fleets-panel">
      <div className="fleets-head">
        <h2 style={{ margin: 0 }}>Fleets</h2>
        <div className="seg" role="group" aria-label="Fleet view mode">
          <button className={mode === 'composition' ? 'on' : ''} aria-pressed={mode === 'composition'}
            onClick={() => setMode('composition')}>Composition</button>
          <button className={mode === 'character' ? 'on' : ''} aria-pressed={mode === 'character'}
            onClick={() => setMode('character')}>Per-character</button>
          {data.by_user_available && (
            <button className={mode === 'user' ? 'on' : ''} aria-pressed={mode === 'user'}
              onClick={() => setMode('user')}>By user</button>
          )}
        </div>
      </div>
      <div className="comp-twoside">
        {data.sides.map((side) => (
          <div key={side.side_kind}>
            {mode === 'composition' && <CompositionView side={side} />}
            {mode === 'character' && <CharacterView side={side} />}
            {mode === 'user' && <UserView side={side} />}
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Add the CSS**

Append to `frontend/src/styles/app.css`:

```css
/* Fleets composition panel */
.fleets-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.7rem; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
.seg button { background: transparent; border: 0; color: var(--text-dim); padding: 0.32rem 0.7rem; font-size: 0.78rem; cursor: pointer; border-right: 1px solid var(--border); }
.seg button:last-child { border-right: 0; }
.seg button.on { background: var(--accent); color: #1a1500; font-weight: 700; }
.comp-twoside { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; }
.comp-side-h { display: flex; align-items: center; justify-content: space-between; padding-bottom: 0.35rem; margin-bottom: 0.45rem; border-bottom: 1px solid var(--border); }
.comp-side-h.friendly { border-color: rgba(76,175,80,0.5); }
.comp-side-h.hostile { border-color: rgba(239,83,80,0.5); }
.comp-side-name { font-weight: 700; text-transform: capitalize; }
.comp-side-name.friendly { color: #7fd18a; }
.comp-side-name.hostile { color: #f3938f; }
.comp-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.16rem 0; font-size: 0.82rem; }
.comp-ship-icon { border-radius: 4px; background: var(--panel-2); flex: none; }
.comp-ship-none { width: 18px; height: 18px; display: inline-block; }
.comp-count { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 2.4rem; }
.comp-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.comp-ship-sub { font-size: 0.72rem; flex: none; }
.comp-lost { color: var(--bad); }
.comp-user-group { margin-bottom: 0.4rem; }
.comp-user-head { font-size: 0.72rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.04em; margin: 0.3rem 0 0.15rem; }
@media (max-width: 40rem) { .comp-twoside { grid-template-columns: 1fr; } }
```

- [ ] **Step 5: Run the test + typecheck**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx && npx tsc --noEmit`
Expected: PASS (3 tests); tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/FleetsPanel.tsx frontend/src/components/FleetsPanel.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): FleetsPanel with composition/per-character/by-user toggle"
```

---

### Task 8: BrDetailPage two-column layout

**Files:**
- Modify: `frontend/src/views/BrDetailPage.tsx`
- Modify: `frontend/src/views/BrDetailPage.test.tsx`
- Modify: `frontend/src/styles/app.css` (page grid)

**Interfaces:**
- Consumes: `FleetGraph`, `MomentDetailPanel` (Task 6), `FleetsPanel` (Task 7).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/BrDetailPage.test.tsx` (match the file's existing mock/render setup; if it mocks `api`, ensure `composition`, `contributions`, `fleetTimeline` are stubbed to resolve empty). Add:

```tsx
  it('lays out fleet graph and detail rail in two columns', async () => {
    // (within the existing describe; uses the file's render helper + api mocks)
    renderBrDetail() // existing helper in this test file
    await waitFor(() => expect(screen.getByTestId('br-detail-grid')).toBeInTheDocument())
    expect(screen.getByTestId('br-col-main')).toBeInTheDocument()
    expect(screen.getByTestId('br-col-side')).toBeInTheDocument()
    // moment detail starts in its empty state
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
  })
```

If `BrDetailPage.test.tsx` does not already mock `api.composition` / `api.fleetTimeline`, extend its `vi.mock('../api', …)` to stub them returning resolved empties (`{ by_user_available: false, sides: [] }` and the `emptyFleet` shape) so the page renders without network.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/BrDetailPage.test.tsx`
Expected: FAIL — `br-detail-grid` test id not found.

- [ ] **Step 3: Restructure the page render**

In `frontend/src/views/BrDetailPage.tsx`:

1. Update imports — replace `import { FleetSection } from '../components/FleetSection'` with:

```tsx
import { FleetGraph } from '../components/FleetGraph'
import { MomentDetailPanel } from '../components/MomentDetailPanel'
import { FleetsPanel } from '../components/FleetsPanel'
```

2. Add lifted state inside `BrDetailPage` next to the other `useState`s:

```tsx
  const [selectedTs, setSelectedTs] = useState<number | null>(null)
```

3. Replace the three stacked sections — the `sides-section`, `fleet-graph-section`, and the standalone summary — by wrapping the graph area in a grid. Concretely, replace the existing `<section data-testid="fleet-graph-section">…</section>` block (lines 651-654) with the two-column grid that hosts Fleets + FleetGraph on the left and MomentDetail + Sides on the right:

```tsx
      <div className="br-detail-grid" data-testid="br-detail-grid">
        <div className="br-col-main" data-testid="br-col-main">
          <section className="panel">
            {id && <FleetsPanel brId={id} reloadKey={sidesVersion} />}
          </section>
          <section data-testid="fleet-graph-section" className="panel">
            <h2 style={{ margin: '0 0 0.75rem' }}>Fleet Graph</h2>
            {id && (
              <FleetGraph brId={id} reloadKey={sidesVersion} selectedTs={selectedTs} onSelectTs={setSelectedTs} />
            )}
          </section>
        </div>
        <div className="br-col-side" data-testid="br-col-side">
          <section className="panel">
            <h3 style={{ margin: '0 0 0.5rem' }}>Moment Detail</h3>
            {id && <MomentDetailPanel brId={id} at={selectedTs} />}
          </section>
          <section data-testid="sides-section" className="panel">
            <details>
              <summary style={{ fontWeight: 600 }}>Sides <span className="dim" style={{ fontWeight: 400 }}>(classify alliances/corps)</span></summary>
              <div style={{ marginTop: '0.6rem' }}>
                {id && <SidesEditor brId={id} onChange={() => setSidesVersion((v) => v + 1)} />}
              </div>
            </details>
          </section>
        </div>
      </div>
```

Remove the now-duplicated standalone `<section data-testid="sides-section">` block (lines 646-649) since Sides now lives in the right rail. Leave the Engagements (filter + `FightList`) and Log Coverage sections where they are — they render full-width below the grid.

- [ ] **Step 4: Add the grid CSS**

Append to `frontend/src/styles/app.css`:

```css
/* BR detail two-column grid */
.br-detail-grid { display: grid; grid-template-columns: 1fr 21rem; gap: 1rem; align-items: start; }
.br-col-main { min-width: 0; display: flex; flex-direction: column; gap: 1rem; }
.br-col-side { display: flex; flex-direction: column; gap: 1rem; position: sticky; top: 0.5rem; }
@media (max-width: 60rem) {
  .br-detail-grid { grid-template-columns: 1fr; }
  .br-col-side { position: static; }
}
```

- [ ] **Step 5: Run tests + typecheck + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: all PASS; production build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/BrDetailPage.tsx frontend/src/views/BrDetailPage.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): two-column BR detail layout (fleets + graph | moment detail + sides)"
```

---

### Task 9: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `uv run pytest -q`
Expected: PASS (all suites including `test_weapons`, `test_composition`, `test_e3_fleet_timeline`).

- [ ] **Step 2: Backend lint + types**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: clean (fix any new findings in the files this plan touched).

- [ ] **Step 3: Frontend suite + types + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 4: Manual smoke (optional but recommended)**

Use the `run` skill / dev server, open a BR detail page, and confirm: two columns; Fleets toggle (Composition/Per-character; By-user only when elevated); clicking a graph moment fills the right rail with weapon icons; kill-marker hover shows the victim pilot name; dates are `YYYY-MM-DD` and times 24h UTC.

- [ ] **Step 5: Commit (if any lint/type fixes were needed)**

```bash
git add -A && git commit -m "chore: lint/type fixes for BR detail redesign"
```

---

## Self-Review

**Spec coverage:**
- (A) Page two-column layout → Task 8. ✓
- (B) Fleets composition / per-character / by-user + FC/HC gate + privacy → Task 5 (data/endpoint/gate) + Task 7 (UI/toggle). ✓
- (C) Weapon classification + exact icon w/ family fallback → Task 2 (classifier) + Task 3 (resolve + payload) + Task 6 (`RowIcon`). ✓
- (D) Victim pilot name on kill markers → Task 4 (data) + Task 6 (tooltip). ✓
- (E) ISO/UTC date-time standard → Task 1 (helpers + sweep), applied in Tasks 6/8. ✓
- (F) `FleetSection` split → Task 6. ✓
- Testing section of spec → per-task tests + Task 9 full suite. ✓

**Placeholder scan:** No TODO/TBD steps; every code step shows code. The composition test's `_seed` was verified against the real `tests/test_association._insert_fight` (it creates the attacker at `attacker_idx=0` with no ship and names characters `Char{id}`), and against the `KillmailAttacker` PK `(killmail_id, attacker_idx)` — `_seed` updates the existing attacker's ship rather than inserting a duplicate, and the test asserts the real resolved name `Char9002`.

**Type consistency:** `Contribution`/`ContributionOut` weapon fields (`module_name`, `icon_type_id`, `weapon_category`) match across backend, schema, frontend, and `RowIcon`. `KillEvent.victim_character_name` matches backend dataclass, schema, frontend type, fixture, and tooltip. `CompositionPilot.user_name` / `CompositionOut.by_user_available` consistent across analytics, schema, endpoint, frontend types, and `FleetsPanel`. `FleetGraph` props (`selectedTs`, `onSelectTs`) consistent between Tasks 6 and 8.
