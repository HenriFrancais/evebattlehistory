# BR Detail Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover zKill ISK-destroyed, replace the 5s snapshot with a two-click time range, enrich the snapshot (target ship, hit quality, effect-count ordering), make composition capsule-free and reship-aware, harden kill-marker clicks, and simplify the BR detail page layout.

**Architecture:** Backend changes thread `zkb.totalValue` through ingest (capture from `/related/` + a `/killID/` backfill), generalise the contributions analytics into a time-range "snapshot", and rework composition into a per-pilot hull-set. Frontend renames the panel to Snapshot with a two-click range selector, adds reship badges, and restructures the page. Side classification stays on the existing `classify_entity` path.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / httpx / pytest (backend); React 18 / TypeScript / Vite / Vitest / uPlot (frontend).

## Global Constraints

- All dates `YYYY-MM-DD`; all times 24h UTC via `frontend/src/format.ts` helpers (`fmtTime`, `fmtDateTime`, `fmtCompact`). Never `toLocale*`.
- Side classification uses `app.analytics.sides_config.classify_entity` with baseline blues (`AppConfig.our_alliance_ids`/`our_corp_ids`) + per-BR overrides from `load_overrides`. Never re-derive sides another way.
- Composition `user_name` / By-user data is FC/HC-only (`can_create_br`); members get `user_name: null`, `by_user_available: false`. Unchanged by this plan.
- Capsule is `type_id` **670**; excluded from every composition view.
- zKill HTTP calls send `User-Agent: nv-br` and must be polite (bounded concurrency, no hammering).
- Backend tests use `db_session_maker` / `make_client` + `CREATOR_HEADERS` / `MEMBER_HEADERS` from `tests/conftest.py`; run `uv run pytest`. Frontend tests mock `../api` and `uplot`; run `cd frontend && npm test`, typecheck `npx tsc --noEmit`.

---

### Task 1: Capture zKill totalValue from `/related/` and persist it

**Files:**
- Modify: `app/ingest/sources/base.py` (`ResolvedBr`)
- Modify: `app/ingest/sources/zkillboard.py` (`_extract_refs_from_related`, `ZkbSource.resolve`, `fetch_window_killmails`)
- Modify: `app/ingest/sources/factory.py` (window path, demo/aurora set empty values)
- Modify: `app/ingest/pipeline.py` (merge values, pass to persist)
- Modify: `app/ingest/persist.py` (`persist_killmails` injects value)
- Test: `tests/test_isk_value.py` (new)

**Interfaces:**
- Produces: `ResolvedBr.values: dict[int, float | None]` (km_id → totalValue); `_extract_refs_from_related(data) -> tuple[list[tuple[int,str]], dict[int, float|None]]`; `persist_killmails(session, killmails_json, names, values=None)`. Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

Create `tests/test_isk_value.py`:

```python
from __future__ import annotations

import datetime as dt

import pytest


def test_extract_refs_captures_total_value():
    from app.ingest.sources.zkillboard import _extract_refs_from_related

    data = {
        "summary": {
            "teamA": {"kills": {
                "111": {"zkb": {"hash": "h1", "totalValue": 1500000.0}},
            }},
            "teamB": {"kills": {
                "222": {"zkb": {"hash": "h2"}},  # no value
            }},
        }
    }
    refs, values = _extract_refs_from_related(data)
    assert refs == [(111, "h1"), (222, "h2")]
    assert values == {111: 1500000.0, 222: None}


@pytest.mark.asyncio
async def test_persist_injects_total_value(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import Killmail
    from app.ingest.persist import persist_killmails
    from sqlalchemy import select

    km = {
        "killmail_id": 111,
        "killmail_time": dt.datetime(2026, 6, 10, 20, 0, 0, tzinfo=dt.UTC),
        "solar_system_id": 31002222,
        "victim": {"character_id": 5, "ship_type_id": 670},
        "attackers": [],
    }
    async with db_session_maker() as session:
        await persist_killmails(session, [km], {}, values={111: 1500000.0})
        await session.commit()

    async with db_session_maker() as session:
        row = (await session.execute(select(Killmail).where(Killmail.killmail_id == 111))).scalar_one()
    assert row.total_value == pytest.approx(1500000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_isk_value.py -q`
Expected: FAIL — `_extract_refs_from_related` returns a list, not a tuple; `persist_killmails` has no `values` param.

- [ ] **Step 3: Add `values` to `ResolvedBr`**

In `app/ingest/sources/base.py`, replace the dataclass:

```python
@dataclass
class ResolvedBr:
    source: str  # "aurora" | "zkb" | "demo"
    source_ref: str  # the parsed ref
    title: str | None
    refs: list[tuple[int, str]]  # (km_id, km_hash)
    values: dict[int, float | None] = field(default_factory=dict)  # km_id → zkb.totalValue
```

Add `field` to the import: `from dataclasses import dataclass, field`.

- [ ] **Step 4: Capture value in the resolver**

In `app/ingest/sources/zkillboard.py`, replace `_extract_refs_from_related` so it returns refs + values, and update both callers:

```python
def _extract_refs_from_related(
    data: object,
) -> tuple[list[tuple[int, str]], dict[int, float | None]]:
    """Extract (killmail_id, hash) pairs and km_id→totalValue from a /api/related/ response.

    Merges teamA.kills and teamB.kills, skipping any entry lacking a valid zkb.hash.
    Deduplicated (first occurrence wins).
    """
    refs: dict[int, str] = {}
    values: dict[int, float | None] = {}
    if not isinstance(data, dict):
        return [], {}
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return [], {}
    for team_key in ("teamA", "teamB"):
        team = summary.get(team_key)
        if not isinstance(team, dict):
            continue
        kills = team.get("kills")
        if not isinstance(kills, dict):
            continue
        for kill_id_str, kill_obj in kills.items():
            if not isinstance(kill_obj, dict):
                continue
            zkb = kill_obj.get("zkb")
            if not isinstance(zkb, dict):
                continue
            km_hash = zkb.get("hash")
            if not isinstance(km_hash, str) or not km_hash:
                continue
            try:
                km_id = int(kill_id_str)
            except ValueError:
                continue
            if km_id not in refs:
                refs[km_id] = km_hash
                tv = zkb.get("totalValue")
                values[km_id] = float(tv) if isinstance(tv, (int, float)) else None
    return list(refs.items()), values
```

In `ZkbSource.resolve`, change the extraction + return:

```python
            if resp.status_code == 200:
                data = resp.json()
                refs, values = _extract_refs_from_related(data)
                if isinstance(data, dict):
                    system_name = data.get("systemName")
                    if isinstance(system_name, str) and system_name:
                        title = f"{system_name} {dt_str}"
            else:
                log.warning(
                    "zkb.api_error",
                    status=resp.status_code,
                    system_id=system_id,
                    dt_str=dt_str,
                )
                values = {}
        ...
        return ResolvedBr(
            source="zkb",
            source_ref=f"{system_id}/{dt_str}",
            title=title,
            refs=refs,
            values=values,
        )
```

(Initialise `values: dict[int, float | None] = {}` next to `refs` at the top of `resolve`.)

In `fetch_window_killmails`, change the return type to `tuple[list[tuple[int, str]], dict[int, float | None]]` and both `return` sites: the error path `return [], {}` and the success path `return _extract_refs_from_related(data)`.

- [ ] **Step 5: Thread values through factory + pipeline**

In `app/ingest/sources/factory.py`, window real-mode path: `refs, values = await fetch_window_killmails(...)`, then `ResolvedBr(source="zkb", source_ref=..., title=label, refs=refs, values=values)`. (Demo/aurora ResolvedBr constructions leave `values` defaulted — they rely on Task 2's backfill.)

In `app/ingest/pipeline.py`, merge values alongside refs. After `all_refs: dict[int, str] = {}` add `all_values: dict[int, float | None] = {}`. In the merge loop replace:

```python
                for km_id, km_hash in resolved.refs:
                    if km_id not in all_refs:
                        all_refs[km_id] = km_hash
                        all_values[km_id] = resolved.values.get(km_id)
```

Pass to persist (Phase 3): `await persist_killmails(session, killmails_json, names, values=all_values)`.

- [ ] **Step 6: Inject the value in persist**

In `app/ingest/persist.py`, change the signature and inject a `zkb` envelope before parse so the existing `parse_killmail` `zkb.totalValue` read fills `total_value`:

```python
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
```

(Leave the rest of `persist_killmails` unchanged.)

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_isk_value.py -q`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add app/ingest/sources/base.py app/ingest/sources/zkillboard.py app/ingest/sources/factory.py app/ingest/pipeline.py app/ingest/persist.py tests/test_isk_value.py
git commit -m "feat(ingest): capture zkb.totalValue from /related/ into Killmail.total_value"
```

---

### Task 2: Backfill missing ISK values from zKill `/killID/`

**Files:**
- Create: `app/ingest/zkb_value.py`
- Modify: `app/ingest/pipeline.py` (call backfill after persist)
- Test: `tests/test_isk_value.py` (extend)

**Interfaces:**
- Consumes: `Killmail.total_value` (Task 1).
- Produces: `async def backfill_killmail_values(session, br_id, settings) -> int` — fetches `https://zkillboard.com/api/killID/{id}/`, reads `zkb.totalValue`, updates null rows; returns count updated.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_isk_value.py`:

```python
@pytest.mark.asyncio
async def test_backfill_fills_null_values(db_session_maker, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import uuid

    from app.config import get_settings
    from app.db.models import BattleReport, BrFight, BrKillmail, Killmail
    from app.ingest import zkb_value
    from sqlalchemy import select

    async with db_session_maker() as session:
        br_id = str(uuid.uuid4())
        session.add(BattleReport(br_id=br_id, source="zkb", source_url="http://x", source_ref="r",
                                 created_by_user="t", status="ready", progress_pct=100,
                                 created_at=dt.datetime.now(dt.UTC)))
        session.add(Killmail(killmail_id=900, killmail_time=dt.datetime(2026, 6, 10, tzinfo=dt.UTC),
                             solar_system_id=31002222, victim_ship_type_id=670,
                             total_value=None, npc_kill=False, solo_kill=False, hash="hh"))
        session.add(BrKillmail(br_id=br_id, killmail_id=900))
        await session.commit()

    async def fake_fetch(client, km_id, km_hash):  # noqa: ANN001
        return 4242.0 if km_id == 900 else None

    monkeypatch.setattr(zkb_value, "_fetch_value", fake_fetch)

    async with db_session_maker() as session:
        n = await zkb_value.backfill_killmail_values(session, br_id, get_settings())
        await session.commit()
    assert n == 1

    async with db_session_maker() as session:
        row = (await session.execute(select(Killmail).where(Killmail.killmail_id == 900))).scalar_one()
    assert row.total_value == pytest.approx(4242.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_isk_value.py::test_backfill_fills_null_values -q`
Expected: FAIL — `ModuleNotFoundError: app.ingest.zkb_value`.

- [ ] **Step 3: Implement the backfill module**

Create `app/ingest/zkb_value.py`:

```python
"""Backfill Killmail.total_value from zKillboard's per-killmail endpoint.

zKill persists the ISK value it calculates at time of destruction. The /related/
resolver captures it when present; this module fills any killmail still missing a
value via GET /api/killID/{id}/, politely (bounded concurrency).
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import BrKillmail, Killmail
from app.observability.logging import log

ZKB_API = "https://zkillboard.com/api"
_MAX_CONCURRENCY = 4


async def _fetch_value(client: httpx.AsyncClient, km_id: int, km_hash: str | None) -> float | None:
    """Return zkb.totalValue for one killmail, or None on any failure."""
    try:
        resp = await client.get(f"{ZKB_API}/killID/{km_id}/")
        if resp.status_code != 200:
            return None
        data = resp.json()
        # /killID/ returns a list with one package: [{"killmail_id":..,"zkb":{"totalValue":..}}]
        pkg = data[0] if isinstance(data, list) and data else data
        zkb = pkg.get("zkb") if isinstance(pkg, dict) else None
        tv = zkb.get("totalValue") if isinstance(zkb, dict) else None
        return float(tv) if isinstance(tv, (int, float)) else None
    except Exception as exc:  # network / shape / json
        log.warning("zkb.value_fetch_failed", km_id=km_id, error=str(exc))
        return None


async def backfill_killmail_values(
    session: AsyncSession, br_id: str, settings: Settings
) -> int:
    """Fill null Killmail.total_value for the BR's killmails from zKill. Returns count updated."""
    if settings.data_source == "demo":
        return 0
    rows = (
        await session.execute(
            select(Killmail.killmail_id, Killmail.hash)
            .join(BrKillmail, BrKillmail.killmail_id == Killmail.killmail_id)
            .where(BrKillmail.br_id == br_id, Killmail.total_value.is_(None))
        )
    ).all()
    if not rows:
        return 0

    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    updated = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "nv-br", "Accept-Encoding": "gzip"}, timeout=30.0
    ) as client:
        async def _one(km_id: int, km_hash: str | None) -> None:
            nonlocal updated
            async with sem:
                value = await _fetch_value(client, km_id, km_hash)
            if value is not None:
                await session.execute(
                    update(Killmail).where(Killmail.killmail_id == km_id).values(total_value=value)
                )
                updated += 1

        await asyncio.gather(*[_one(int(kid), kh) for kid, kh in rows])
    return updated
```

- [ ] **Step 4: Wire into the pipeline**

In `app/ingest/pipeline.py`, right after the Phase-3 persist block commits (after the `await session.commit()` that follows `persist_killmails`), add a backfill phase:

```python
        # Phase 3.5: backfill any ISK values not provided by /related/.
        try:
            async with session_maker() as session:
                from app.ingest.zkb_value import backfill_killmail_values

                filled = await backfill_killmail_values(session, br_id, settings)
                await session.commit()
                log.info("pipeline.isk_backfill", br_id=br_id, filled=filled)
        except Exception as exc:
            log.warning("pipeline.isk_backfill_failed", br_id=br_id, error=str(exc))
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_isk_value.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/ingest/zkb_value.py app/ingest/pipeline.py tests/test_isk_value.py
git commit -m "feat(ingest): backfill missing ISK values via zKill /killID/"
```

---

### Task 3: BR detail `systems` field

**Files:**
- Modify: `app/api/schemas.py` (`BrDetail`)
- Modify: `app/api/brs.py` (`get_br`)
- Modify: `frontend/src/api.ts` (`BrDetail`)
- Test: `tests/test_e4a_multi_source.py` or a focused new test — use `tests/test_br_systems.py` (new)

**Interfaces:**
- Produces: `BrDetail.systems: list[str]` — distinct resolved `SolarSystem.name`s of the BR's fights. Consumed by Task 9.

- [ ] **Step 1: Write the failing test**

Create `tests/test_br_systems.py`:

```python
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db.models import BattleReport, BrFight, Fight, SolarSystem


@pytest.mark.asyncio
async def test_br_detail_lists_distinct_system_names(make_client, db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import CREATOR_HEADERS

    async with db_session_maker() as session:
        session.add(SolarSystem(system_id=31002222, name="J164805"))
        f = Fight(system_id=31002222, started_at=dt.datetime(2026, 6, 10, tzinfo=dt.UTC),
                  ended_at=dt.datetime(2026, 6, 10, tzinfo=dt.UTC), isk_destroyed_total=0.0,
                  largest_side_pilots=1)
        session.add(f)
        await session.flush()
        br_id = str(uuid.uuid4())
        session.add(BattleReport(br_id=br_id, source="demo", source_url="http://x", source_ref="r",
                                 created_by_user="t", status="ready", progress_pct=100,
                                 created_at=dt.datetime.now(dt.UTC)))
        session.add(BrFight(br_id=br_id, fight_id=f.fight_id, seq=0))
        await session.commit()

    # (Use the booted-app pattern from test_e3 for the HTTP contract, or call get_br directly.)
    from app.api.brs import get_br
    detail = await _call_get_br(br_id, db_session_maker)  # helper below
    assert detail.systems == ["J164805"]


async def _call_get_br(br_id, db_session_maker):  # type: ignore[no-untyped-def]
    from app.api.brs import get_br
    async with db_session_maker() as session:
        return await get_br(br_id, session)
```

(If `SolarSystem`'s PK column is not `system_id`, match the real column name from `app/db/models.py` — confirm before writing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_br_systems.py -q`
Expected: FAIL — `BrDetail` has no `systems`.

- [ ] **Step 3: Add the field to the schema**

In `app/api/schemas.py`, extend `BrDetail`:

```python
class BrDetail(BrSummary):
    fights: list[FightOut]
    systems: list[str] = []
```

- [ ] **Step 4: Populate it in `get_br`**

In `app/api/brs.py` `get_br`, before the return, resolve distinct system names from the loaded fights:

```python
    fights = await _load_fights(session, br_id)

    sys_ids = [f.system_id for f in fights]
    system_names: list[str] = []
    if sys_ids:
        from app.db.models import SolarSystem

        name_map = {
            s.system_id: s.name
            for s in (
                await session.execute(select(SolarSystem).where(SolarSystem.system_id.in_(sys_ids)))
            ).scalars()
        }
        seen: set[str] = set()
        for sid in sys_ids:
            nm = name_map.get(sid) or f"System {sid}"
            if nm not in seen:
                seen.add(nm)
                system_names.append(nm)

    return BrDetail(
        **_br_to_summary(br).model_dump(),
        fights=fights,
        systems=system_names,
    )
```

- [ ] **Step 5: Update the frontend type**

In `frontend/src/api.ts`, extend `BrDetail`:

```ts
export interface BrDetail extends BrSummary {
  fights: FightOut[]
  systems: string[]
}
```

- [ ] **Step 6: Run tests + typecheck**

Run: `uv run pytest tests/test_br_systems.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS; tsc clean.

- [ ] **Step 7: Commit**

```bash
git add app/api/schemas.py app/api/brs.py frontend/src/api.ts tests/test_br_systems.py
git commit -m "feat: BR detail exposes distinct system names"
```

---

### Task 4: Snapshot analytics — time range, target ship, hit quality

**Files:**
- Modify: `app/analytics/fleet.py` (`fleet_contributions` → `fleet_snapshot`; `Contribution`)
- Modify: `app/api/schemas.py` (`ContributionOut`, `ContributionsOut`)
- Modify: `app/api/fleet.py` (`get_contributions` → `get_snapshot`)
- Modify: `frontend/src/api.ts` (`Contribution`, `ContributionsResponse`, `api.snapshot`)
- Modify: `tests/test_e3_fleet_timeline.py` (snapshot tests)

**Interfaces:**
- Produces: `fleet_snapshot(session, br_id, from_ts, to_ts, settings) -> list[Contribution]`; `Contribution` gains `target_ship: str | None`, `quality: str | null`. Endpoint `GET /api/brs/{id}/snapshot?from=&to=` → `ContributionsOut` with `from_ts`, `to_ts`, `rows`. Frontend `api.snapshot(brId, from, to)`. Consumed by Task 6.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_e3_fleet_timeline.py`:

```python
async def test_fleet_snapshot_range_ship_and_quality(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    import datetime as _dt

    from app.analytics.fleet import fleet_snapshot
    from app.config import get_settings
    from app.db.models import GamelogFile, LogEvent

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        gf = GamelogFile(uploaded_by_user="u", claimed_character_id=CHAR_A, resolved_via="filename",
                         stored_path="/x", sha256="qq", mime="text/plain", size=1,
                         parse_status="parsed", event_count=3,
                         uploaded_at=_dt.datetime.now(_dt.UTC))
        session.add(gf)
        await session.flush()
        t0 = BUCKET_TS_1                       # 20:00:00
        t1 = BUCKET_TS_2                       # 20:00:05
        t_out = t1 + _dt.timedelta(seconds=30)  # outside the window
        # Two damage hits on Enemy1 (Loki) with differing quality + one outside-range hit.
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t0, effect_type="damage",
                             direction="out", amount=300.0, quality="Smashes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t1, effect_type="damage",
                             direction="out", amount=100.0, quality="Smashes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        session.add(LogEvent(file_id=gf.file_id, character_id=CHAR_A, ts=t_out, effect_type="damage",
                             direction="out", amount=999.0, quality="Grazes",
                             other_name="Enemy1", other_ship_name="Loki", fight_id=fight_id))
        await session.commit()

    frm = int(t0.timestamp())
    to = int(t1.timestamp()) + 1
    async with db_session_maker() as session:
        rows = await fleet_snapshot(session, br_id, frm, to, get_settings())

    enemy = next(r for r in rows if r.target_name == "Enemy1")
    assert enemy.target_ship == "Loki"
    assert enemy.value == pytest.approx(400.0)   # 300 + 100; the 999 is outside the range
    assert enemy.quality == "Smashes"            # dominant quality
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e3_fleet_timeline.py::test_fleet_snapshot_range_ship_and_quality -q`
Expected: FAIL — `cannot import name 'fleet_snapshot'`.

- [ ] **Step 3: Extend the `Contribution` dataclass**

In `app/analytics/fleet.py`, add two fields to `Contribution` (after `weapon_category`):

```python
    module_name: str | None = None
    icon_type_id: int | None = None
    weapon_category: str | None = None
    target_ship: str | None = None
    quality: str | None = None
```

- [ ] **Step 4: Rewrite `fleet_contributions` as `fleet_snapshot`**

In `app/analytics/fleet.py`, replace the `fleet_contributions` function definition line and its bucket math with a range, select `other_ship_name` + `quality`, and compute dominant quality. Rename the function to `fleet_snapshot` and change its window:

```python
async def fleet_snapshot(
    session: AsyncSession, br_id: str, from_ts: int, to_ts: int, settings: Settings
) -> list[Contribution]:
    """Break down ALL activity in the half-open window [from_ts, to_ts) into source→target
    rows, grouped by (source, target+ship, effect, direction), sorted by value desc. Damage
    rows resolve a weapon icon and a dominant hit-quality. (from_ts > to_ts is swapped.)"""
    fight_ids = list(
        (await session.execute(select(BrFight.fight_id).where(BrFight.br_id == br_id))).scalars()
    )
    if not fight_ids:
        return []
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts
    start = dt.datetime.fromtimestamp(from_ts, tz=dt.UTC).replace(tzinfo=None)
    end = dt.datetime.fromtimestamp(to_ts, tz=dt.UTC).replace(tzinfo=None)

    rows = (
        await session.execute(
            select(
                LogEvent.character_id,
                LogEvent.other_name,
                LogEvent.other_ship_name,
                LogEvent.effect_type,
                LogEvent.direction,
                LogEvent.amount,
                LogEvent.module_name,
                LogEvent.quality,
            ).where(
                LogEvent.fight_id.in_(fight_ids),
                LogEvent.ts >= start,
                LogEvent.ts < end,
                LogEvent.effect_type.in_(_KNOWN_EFFECTS),
            )
        )
    ).all()

    Key = tuple[int | None, str, str, str, str]  # (cid, target, ship, eff, dir)
    agg: dict[Key, float] = {}
    module_dmg: dict[Key, dict[str, float]] = {}
    quality_ct: dict[Key, dict[str, int]] = {}
    for cid, other, oship, eff, direction, amount, module, quality in rows:
        key = (cid, _clean_target_name(other), _clean_target_name(oship), eff or "", direction or "")
        contrib = 1.0 if eff in _COUNT_EFFECTS else abs(amount or 0.0)
        agg[key] = agg.get(key, 0.0) + contrib
        if eff == "damage":
            if module:
                module_dmg.setdefault(key, {})
                module_dmg[key][module] = module_dmg[key].get(module, 0.0) + abs(amount or 0.0)
            if quality:
                quality_ct.setdefault(key, {})
                quality_ct[key][quality] = quality_ct[key].get(quality, 0) + 1

    top_module: dict[Key, str] = {
        k: max(m.items(), key=lambda kv: kv[1])[0] for k, m in module_dmg.items()
    }
    wanted: set[str] = set()
    for name in top_module.values():
        wanted.add(name)
        fb = classify_weapon(name).fallback_name
        if fb:
            wanted.add(fb)
    name_to_type: dict[str, int] = {}
    if wanted:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.name.in_(wanted)))
        ).scalars():
            name_to_type[inv.name] = inv.type_id

    names = await _resolve_char_names(session, settings, {k[0] for k in agg if k[0] is not None})

    out: list[Contribution] = []
    for (cid, other, oship, eff, direction), val in agg.items():
        key = (cid, other, oship, eff, direction)
        module = top_module.get(key)
        icon_type_id: int | None = None
        category: str | None = None
        if module is not None:
            wc = classify_weapon(module)
            category = wc.category
            icon_type_id = name_to_type.get(module) or (
                name_to_type.get(wc.fallback_name) if wc.fallback_name else None
            )
        quality: str | None = None
        if key in quality_ct:
            quality = max(quality_ct[key].items(), key=lambda kv: kv[1])[0]
        out.append(
            Contribution(
                source_character_id=cid,
                source_name=(names.get(cid) or f"Char {cid}") if cid is not None else "?",
                target_name=other,
                target_ship=oship or None,
                effect_type=eff,
                direction=direction,
                group=_EFFECT_GROUP.get(eff, "other"),
                value=val,
                module_name=module,
                icon_type_id=icon_type_id,
                weapon_category=category,
                quality=quality,
            )
        )
    out.sort(key=lambda c: c.value, reverse=True)
    return out
```

(`_clean_target_name` already maps falsy → `"?"`; for the ship slot, convert `"?"` to None at the Contribution by `oship or None` — i.e. when `other_ship_name` is empty, `_clean_target_name` returns `"?"`; replace that: use `target_ship=(oship if oship != "?" else None)`. Apply that exact conditional.)

- [ ] **Step 5: Update schemas + endpoint**

In `app/api/schemas.py`, extend `ContributionOut` (add after `weapon_category`):

```python
    weapon_category: str | None = None
    target_ship: str | None = None
    quality: str | None = None
```

Replace `ContributionsOut`:

```python
class ContributionsOut(BaseModel):
    from_ts: int
    to_ts: int
    rows: list[ContributionOut]
```

In `app/api/fleet.py`, replace `get_contributions` with `get_snapshot`:

```python
@router.get("/api/brs/{br_id}/snapshot")
async def get_snapshot(
    br_id: str, session: SessionDep, from_ts: int, to_ts: int
) -> ContributionsOut:
    """All source→target activity in [from_ts, to_ts), grouped + sorted by value."""
    await _require_br(br_id, session)
    contribs = await fleet_snapshot(br_id and br_id, ...)  # see exact call below
```

Use this exact body:

```python
@router.get("/api/brs/{br_id}/snapshot")
async def get_snapshot(
    br_id: str, session: SessionDep, from_ts: int, to_ts: int
) -> ContributionsOut:
    """All source→target activity in the half-open window [from_ts, to_ts)."""
    await _require_br(br_id, session)
    contribs = await fleet_snapshot(session, br_id, from_ts, to_ts, get_settings())
    return ContributionsOut(
        from_ts=from_ts,
        to_ts=to_ts,
        rows=[
            ContributionOut(
                source_character_id=c.source_character_id,
                source_name=c.source_name,
                target_name=c.target_name,
                target_ship=c.target_ship,
                effect_type=c.effect_type,
                direction=c.direction,
                group=c.group,
                value=c.value,
                module_name=c.module_name,
                icon_type_id=c.icon_type_id,
                weapon_category=c.weapon_category,
                quality=c.quality,
            )
            for c in contribs
        ],
    )
```

Update the import in `app/api/fleet.py` from `fleet_contributions` to `fleet_snapshot` and remove the old `at`-based query param + `BUCKET_SECONDS` import if now unused (keep `BUCKET_SECONDS` — it's still used by fleet-timeline). Delete the old `get_contributions` function.

- [ ] **Step 6: Update the frontend client + type**

In `frontend/src/api.ts`, extend `Contribution` (add `target_ship`, `quality`), replace `ContributionsResponse`, and replace the `contributions` method:

```ts
export interface Contribution {
  source_character_id: number | null
  source_name: string
  target_name: string
  target_ship: string | null
  effect_type: string
  direction: string
  group: string
  value: number
  module_name: string | null
  icon_type_id: number | null
  weapon_category: string | null
  quality: string | null
}

export interface ContributionsResponse {
  from_ts: number
  to_ts: number
  rows: Contribution[]
}
```

Replace the `contributions` entry in the `api` object:

```ts
  snapshot: (brId: string, from: number, to: number) =>
    jsonFetch<ContributionsResponse>(`${API}/brs/${brId}/snapshot?from_ts=${from}&to_ts=${to}`),
```

- [ ] **Step 7: Run tests + typecheck**

Run: `uv run pytest tests/test_e3_fleet_timeline.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS (the snapshot test + existing ones; the old `at`-based API contributions test, if any, is updated/removed); tsc fails only where `MomentDetailPanel`/`BrDetailPage` still call `api.contributions` — those are fixed in Tasks 6 & 9, so run `npx tsc --noEmit` again after them. For THIS task, confirm `app/` + `api.ts` compile; the two consuming components are updated next.

> If a frontend test references `api.contributions` (e.g. `MomentDetailPanel.test.tsx`), leave it — Task 6 rewrites that test. Backend + `api.ts` are the deliverable here.

- [ ] **Step 8: Commit**

```bash
git add app/analytics/fleet.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_e3_fleet_timeline.py
git commit -m "feat: snapshot analytics over a time range with target ship + hit quality"
```

---

### Task 5: Composition — exclude capsules, model reships

**Files:**
- Modify: `app/analytics/composition.py`
- Modify: `app/api/schemas.py` (`CompositionShipOut`, `CompositionPilotOut`)
- Modify: `app/api/fleet.py` (composition mapping)
- Modify: `frontend/src/api.ts` (`CompositionShip`, `CompositionPilot`)
- Modify: `tests/test_composition.py`

**Interfaces:**
- Produces: `CompositionPilot` gains `reship: bool`; pilots are one-per-(character,hull); capsule 670 excluded; `CompositionPilotOut`/`CompositionShipOut` gain `reship`. Consumed by Task 8.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_composition.py` (reuses `_seed`, `ABSOLUTION`, `ATTACKER`, `VICTIM`):

```python
CAPSULE = 670
GUARDIAN = 11987


@pytest.mark.asyncio
async def test_composition_excludes_capsules_and_flags_reships(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.composition import fleet_composition
    from app.config import get_settings
    from app.db.models import InventoryType, Killmail, KillmailAttacker, FightKill

    async with db_session_maker() as session:
        br_id, fight_id = await _seed(session)  # ATTACKER flies Absolution (attacker_idx 0)
        session.add(InventoryType(type_id=GUARDIAN, name="Guardian"))
        session.add(InventoryType(type_id=CAPSULE, name="Capsule"))
        km_id = (await session.execute(
            select(FightKill.killmail_id).where(FightKill.fight_id == fight_id)
        )).scalar_one()
        # Reship: same ATTACKER also appears in a Guardian on the same killmail.
        session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=1, character_id=ATTACKER,
                                     ship_type_id=GUARDIAN, damage_done=1, final_blow=False))
        # A capsule victim for ATTACKER (podded) must NOT add a Capsule hull.
        session.add(Killmail(killmail_id=km_id + 1,
                             killmail_time=dt.datetime(2026, 6, 10, 20, 1, tzinfo=dt.UTC),
                             solar_system_id=31002222, victim_character_id=ATTACKER,
                             victim_ship_type_id=CAPSULE, npc_kill=False, solo_kill=False))
        session.add(FightKill(fight_id=fight_id, killmail_id=km_id + 1, side_idx=0))
        await session.commit()

    async with db_session_maker() as session:
        result = await fleet_composition(
            session, br_id, baseline_alliances=set(), baseline_corps=set(),
            overrides={}, settings=get_settings(), char_to_user=None,
        )

    pilots = [p for s in result.sides for p in s.pilots]
    hulls = {p.ship_name for p in pilots if p.character_id == ATTACKER}
    assert hulls == {"Absolution", "Guardian"}            # both hulls, capsule excluded
    assert all(p.reship for p in pilots if p.character_id == ATTACKER)
    assert not any(p.ship_name == "Capsule" for p in pilots)
    # ATTACKER counted once toward pilot_count despite two hulls
    side = next(s for s in result.sides if any(p.character_id == ATTACKER for p in s.pilots))
    assert sum(1 for p in side.pilots if p.character_id == ATTACKER) == 2  # two hull rows
    assert side.pilot_count == len({p.character_id for p in side.pilots})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py::test_composition_excludes_capsules_and_flags_reships -q`
Expected: FAIL — `CompositionPilot` has no `reship`; capsules present; one-ship-per-pilot.

- [ ] **Step 3: Rewrite the composition model**

In `app/analytics/composition.py`, add the capsule constant near the top and replace the dataclasses + `fleet_composition` body. Add:

```python
CAPSULE_TYPE_ID = 670
```

Replace `CompositionPilot` and the internal `_Pilot`:

```python
@dataclass
class CompositionPilot:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str
    lost: bool
    reship: bool
    user_name: str | None


@dataclass
class _Acc:
    side: str
    hulls: dict[int, bool]  # non-capsule ship_type_id → lost?
    podded: bool            # appeared only in a capsule
```

Replace the body of `fleet_composition` from the `pilots: dict[int, _Pilot] = {}` block through the `return CompositionResult(...)` with:

```python
    acc: dict[int, _Acc] = {}

    def _ensure(char_id: int, side: str) -> _Acc:
        a = acc.get(char_id)
        if a is None:
            a = _Acc(side=side, hulls={}, podded=False)
            acc[char_id] = a
        return a

    # Victims: authoritative side + a lost hull (capsules → podded, not a hull).
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
        a = _ensure(char_id, _side(alli, corp))
        a.side = _side(alli, corp)  # victim entity wins for side
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls[ship_id] = True
        elif ship_id == CAPSULE_TYPE_ID:
            a.podded = True

    # Attackers: side if unseen, plus any non-capsule hull they flew.
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
        a = acc.get(char_id) or _ensure(char_id, _side(alli, corp))
        if ship_id is not None and ship_id != CAPSULE_TYPE_ID:
            a.hulls.setdefault(ship_id, False)

    char_names = await _resolve_char_names(session, settings, set(acc))
    ship_ids = {sid for a in acc.values() for sid in a.hulls}
    ship_names: dict[int, str] = {}
    if ship_ids:
        for inv in (
            await session.execute(select(InventoryType).where(InventoryType.type_id.in_(ship_ids)))
        ).scalars():
            ship_names[inv.type_id] = inv.name

    by_side: dict[str, list[CompositionPilot]] = {}
    for char_id, a in acc.items():
        name = char_names.get(char_id) or f"Char {char_id}"
        user = char_to_user.get(char_id) if char_to_user else None
        is_reship = len(a.hulls) > 1
        if a.hulls:
            for sid, lost in a.hulls.items():
                by_side.setdefault(a.side, []).append(
                    CompositionPilot(character_id=char_id, character_name=name, ship_type_id=sid,
                                     ship_name=ship_names.get(sid, "Unknown"), lost=lost,
                                     reship=is_reship, user_name=user)
                )
        else:
            # Capsule-only / no hull recorded.
            by_side.setdefault(a.side, []).append(
                CompositionPilot(character_id=char_id, character_name=name, ship_type_id=None,
                                 ship_name="Unknown", lost=a.podded, reship=False, user_name=user)
            )

    sides: list[CompositionSide] = []
    for side_kind in _SIDE_ORDER:
        plist = by_side.get(side_kind)
        if not plist:
            continue
        plist.sort(key=lambda x: (x.ship_name, x.character_name))
        counts: Counter[int] = Counter()
        for pilot in plist:
            if pilot.ship_type_id is not None:
                counts[pilot.ship_type_id] += 1
        ships = [
            CompositionShip(ship_type_id=sid, ship_name=ship_names.get(sid, "Unknown"), count=c)
            for sid, c in counts.most_common()
        ]
        pilot_count = len({p.character_id for p in plist})
        sides.append(CompositionSide(side_kind=side_kind, pilot_count=pilot_count,
                                     ships=ships, pilots=plist))
    return CompositionResult(sides=sides)
```

Delete the now-unused old `_Pilot` dataclass and its references.

- [ ] **Step 4: Add `reship` to schema + endpoint mapping**

In `app/api/schemas.py`, add `reship: bool = False` to `CompositionPilotOut` (after `lost`). In `app/api/fleet.py`, the composition `CompositionPilotOut(...)` mapping adds `reship=p.reship`.

- [ ] **Step 5: Frontend types**

In `frontend/src/api.ts`, add `reship: boolean` to `CompositionPilot` (after `lost`).

- [ ] **Step 6: Run tests + typecheck**

Run: `uv run pytest tests/test_composition.py -q && cd frontend && npx tsc --noEmit`
Expected: backend PASS (new + existing composition tests; note `test_composition_counts_ships_per_side` still holds — ATTACKER in one Absolution → reship False, one row); tsc clean.

> The existing `test_composition_counts_ships_per_side` asserts `pilot.lost is False` and one Absolution row — still true. It does not check `reship`; leave it. If it referenced `_Pilot`, update to the new shape.

- [ ] **Step 7: Commit**

```bash
git add app/analytics/composition.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts tests/test_composition.py
git commit -m "feat: composition excludes capsules and models reships (hull set per pilot)"
```

---

### Task 6: SnapshotPanel (rename, range fetch, Name (Ship) headers, ordering, quality)

**Files:**
- Rename/rewrite: `frontend/src/components/MomentDetailPanel.tsx` → `frontend/src/components/SnapshotPanel.tsx`
- Rename/rewrite: `frontend/src/components/MomentDetailPanel.test.tsx` → `frontend/src/components/SnapshotPanel.test.tsx`
- Modify: `frontend/src/styles/app.css` (quality tag, reship badge styles shared)

**Interfaces:**
- Consumes: `api.snapshot`, `Contribution` (Task 4).
- Produces: `SnapshotPanel({ brId, range })` where `range: { from: number; to: number } | null`. Header text "Snapshot". Empty state `data-testid="moment-detail-empty"` retained. Consumed by Task 9.

Read the current `MomentDetailPanel.tsx` first. Apply these changes (keep the `fmtCompact`/`fmtTime` imports, the `EFFECT_ICON`/`EFFECT_LABEL`/`RowIcon` logic, the `focus-*` markup):

- [ ] **Step 1: Rewrite the test**

Create `frontend/src/components/SnapshotPanel.test.tsx` (delete the old `MomentDetailPanel.test.tsx`):

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse } from '../api'
import { SnapshotPanel } from './SnapshotPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, snapshot: vi.fn() } }
})
import { api } from '../api'

const resp: ContributionsResponse = {
  from_ts: 1000, to_ts: 1010,
  rows: [
    // Loki target: 2 source rows → busiest, must sort first
    { source_character_id: 1, source_name: 'Talun', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 900,
      module_name: '250mm Railgun II', icon_type_id: 3174, weapon_category: 'hybrid', quality: 'Smashes' },
    { source_character_id: 2, source_name: 'Aiden', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 100,
      module_name: 'Hammerhead II', icon_type_id: 2185, weapon_category: 'drone', quality: 'Penetrates' },
    // Nestor target: 1 source row → single-source, must sink to bottom
    { source_character_id: 3, source_name: 'Sera', target_name: 'Toni', target_ship: 'Nestor',
      effect_type: 'rep_armor', direction: 'in', group: 'damage', value: 8000,
      module_name: null, icon_type_id: null, weapon_category: null, quality: null },
  ],
}

describe('SnapshotPanel', () => {
  beforeEach(() => vi.mocked(api.snapshot).mockReset())

  it('hint when no range selected; no fetch', () => {
    render(<SnapshotPanel brId="br1" range={null} />)
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
    expect(api.snapshot).not.toHaveBeenCalled()
  })

  it('fetches the range and heads groups with Name (Ship), busiest first', async () => {
    vi.mocked(api.snapshot).mockResolvedValue(resp)
    render(<SnapshotPanel brId="br1" range={{ from: 1000, to: 1010 }} />)
    await waitFor(() => expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument())
    expect(api.snapshot).toHaveBeenCalledWith('br1', 1000, 1010)
    const heads = screen.getAllByTestId('focus-card-head').map((e) => e.textContent)
    expect(heads[0]).toMatch(/Crash \(Loki\)/)        // busiest (2 rows) on top
    expect(heads[heads.length - 1]).toMatch(/Toni \(Nestor\)/)  // single-source at bottom
    // quality tag present for a damage row
    expect(screen.getByText(/Smashes/)).toBeInTheDocument()
    // weapon icon
    expect((screen.getByTitle('250mm Railgun II') as HTMLImageElement).src).toContain('/types/3174/')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/SnapshotPanel.test.tsx`
Expected: FAIL — cannot resolve `./SnapshotPanel`.

- [ ] **Step 3: Create `SnapshotPanel.tsx`**

Create `frontend/src/components/SnapshotPanel.tsx` from the current `MomentDetailPanel.tsx` with these changes: rename the component, take `range` instead of `at`, fetch via `api.snapshot`, group by `target_name + target_ship` with header `Name (Ship)` and a `data-testid="focus-card-head"`, order groups by row count desc (tiebreak total), and render a quality tag. Full file:

```tsx
// Snapshot: source→target breakdown for a selected time range.
import { useEffect, useState } from 'react'
import type { Contribution, ContributionsResponse } from '../api'
import { api } from '../api'
import { fmtCompact, fmtTime } from '../format'

const EFFECT_ICON: Record<string, number> = {
  damage: 485, rep_armor: 11355, rep_shield: 3586, neut: 533, nos: 530,
  cap_transfer: 529, scram: 447, disrupt: 3242, jam: 1957,
}
const EFFECT_LABEL: Record<string, string> = {
  damage: 'damage', rep_armor: 'armor rep', rep_shield: 'shield rep', neut: 'neut',
  nos: 'nos', cap_transfer: 'cap', scram: 'scram', disrupt: 'point', jam: 'jam',
}

function RowIcon({ row }: { row: Contribution }) {
  if (row.effect_type === 'damage' && row.icon_type_id != null) {
    return (
      <img className="contrib-eff-icon"
        src={`https://images.evetech.net/types/${row.icon_type_id}/icon?size=32`}
        alt={row.module_name ?? 'weapon'} title={row.module_name ?? undefined} width={18} height={18} />
    )
  }
  const id = EFFECT_ICON[row.effect_type]
  if (id == null) return <span className="contrib-eff-dot" />
  return (
    <img className="contrib-eff-icon"
      src={`https://images.evetech.net/types/${id}/icon?size=32`}
      alt={EFFECT_LABEL[row.effect_type] ?? row.effect_type}
      title={row.module_name ?? EFFECT_LABEL[row.effect_type] ?? row.effect_type} width={18} height={18} />
  )
}

interface TargetGroup { target: string; ship: string | null; total: number; rows: Contribution[] }

function groupByTarget(rows: Contribution[]): TargetGroup[] {
  const map = new Map<string, TargetGroup>()
  for (const r of rows) {
    const key = `${r.target_name} ${r.target_ship ?? ''}`
    let g = map.get(key)
    if (!g) { g = { target: r.target_name, ship: r.target_ship, total: 0, rows: [] }; map.set(key, g) }
    g.total += r.value
    g.rows.push(r)
  }
  const groups = [...map.values()]
  for (const g of groups) g.rows.sort((a, b) => b.value - a.value)
  // Busiest targets (most effect rows) first; single-source effects sink to the bottom.
  groups.sort((a, b) => b.rows.length - a.rows.length || b.total - a.total)
  return groups
}

function TargetCard({ group }: { group: TargetGroup }) {
  const head = group.ship ? `${group.target} (${group.ship})` : group.target
  return (
    <div className="focus-card">
      <div className="focus-card-head" data-testid="focus-card-head" title={head}>{head}</div>
      {group.rows.slice(0, 12).map((r, i) => (
        <div className="focus-row" key={i}>
          <RowIcon row={r} />
          <span className="focus-src" title={r.source_name}>{r.source_name}</span>
          {r.quality && <span className="focus-quality" title="dominant hit quality">{r.quality}</span>}
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

export function SnapshotPanel({ brId, range }: { brId: string; range: { from: number; to: number } | null }) {
  const [data, setData] = useState<ContributionsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (range == null) { setData(null); return }
    setLoading(true); setError(null)
    let cancelled = false
    const handle = setTimeout(() => {
      api.snapshot(brId, Math.round(range.from), Math.round(range.to)).then(
        (d) => { if (!cancelled) { setData(d); setLoading(false) } },
        (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
      )
    }, 120)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [brId, range])

  if (range == null) {
    return (
      <div className="contrib-panel" data-testid="moment-detail-empty">
        <p className="dim" style={{ fontSize: '0.8rem', textAlign: 'center', padding: '1rem 0' }}>
          Click a START then an END point on any graph to snapshot that window.
        </p>
      </div>
    )
  }

  const rows = data?.rows ?? []
  const targets = groupByTarget(rows)
  return (
    <div className="contrib-panel" data-testid="fleet-contrib">
      <div className="contrib-head">
        <strong>{fmtTime(range.from, true)} → {fmtTime(range.to, true)} UTC</strong>
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
        {targets.map((g) => <TargetCard key={`${g.target}-${g.ship ?? ''}`} group={g} />)}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Add CSS for the quality tag**

Append to `frontend/src/styles/app.css`:

```css
.focus-quality { flex: none; font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--warn); border: 1px solid var(--border); border-radius: 3px; padding: 0 0.2rem; }
```

- [ ] **Step 5: Run the test + typecheck**

Run: `cd frontend && npx vitest run src/components/SnapshotPanel.test.tsx && npx tsc --noEmit`
Expected: PASS; tsc fails only where `BrDetailPage` still imports `MomentDetailPanel` (fixed in Task 9).

- [ ] **Step 6: Commit**

```bash
git rm frontend/src/components/MomentDetailPanel.tsx frontend/src/components/MomentDetailPanel.test.tsx
git add frontend/src/components/SnapshotPanel.tsx frontend/src/components/SnapshotPanel.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): SnapshotPanel — range fetch, Name (Ship) headers, busiest-first, quality tag"
```

---

### Task 7: FleetGraph — two-click range selector + ctrl-click kill markers

**Files:**
- Modify: `frontend/src/components/FleetGraph.tsx`
- Modify: `frontend/src/components/FleetSection.tsx` (wrapper still compiles; see note)
- Modify: `frontend/src/styles/app.css` (range band styles)
- Modify: `frontend/src/components/FleetSection.test.tsx` (range prop)

**Interfaces:**
- Produces: `FleetGraph` props change from `selectedTs: number | null` / `onSelectTs` to `selectedRange: { from: number; to: number } | null` / `onSelectRange: (r: { from: number; to: number } | null) => void`. Consumed by Task 9.

Read the current `FleetGraph.tsx` first. Changes:

- [ ] **Step 1: Kill-marker ctrl-click**

In `killMarkersPlugin`, change the marker click handler so a plain click does NOT open zKill and does NOT stop propagation (so the graph's range click still fires); only ctrl/cmd-click opens zKill:

```tsx
      el.addEventListener('click', (ev) => {
        if (ev.ctrlKey || ev.metaKey) {
          ev.stopPropagation()
          window.open(`https://zkillboard.com/kill/${k.killmail_id}/`, '_blank', 'noopener,noreferrer')
        }
        // plain click falls through to the graph's range handler
      })
```

In `showTip`, add a hint line to the tooltip HTML (before the closing `</div>`): append `<div class="kill-tip-meta">⌃-click → zKill</div>`.

- [ ] **Step 2: Replace the slider plugin with a range plugin**

Replace `sliderPlugin` with `rangePlugin`. It draws a shaded band between START and END plus two draggable handles, reads the range from a ref, and reports edits. Handle drags `stopPropagation` so native zoom is unaffected:

```tsx
// Persistent draggable range band. Two handles (from/to) + a shaded span in u.over.
// A registered reposition fn keeps all panels' bands in sync. Dragging a handle edits
// the shared range; the band/handles never trigger native drag-zoom (stopPropagation).
function rangePlugin(
  getRange: () => { from: number; to: number } | null,
  onChange: (r: { from: number; to: number }) => void,
  register: (fn: () => void) => () => void,
): uPlot.Plugin {
  let band: HTMLDivElement | null = null
  let h0: HTMLDivElement | null = null
  let h1: HTMLDivElement | null = null
  let unregister: (() => void) | null = null

  const position = (u: uPlot) => {
    const r = getRange()
    if (!band || !h0 || !h1) return
    if (r == null) {
      band.style.display = h0.style.display = h1.style.display = 'none'
      return
    }
    const x0 = u.valToPos(r.from, 'x')
    const x1 = u.valToPos(r.to, 'x')
    const lo = Math.min(x0, x1)
    const hi = Math.max(x0, x1)
    band.style.display = ''
    band.style.left = `${lo}px`
    band.style.width = `${Math.max(0, hi - lo)}px`
    h0.style.display = h1.style.display = ''
    h0.style.left = `${x0}px`
    h1.style.left = `${x1}px`
  }

  const dragHandle = (u: uPlot, which: 'from' | 'to') => (ev: MouseEvent) => {
    ev.stopPropagation()
    ev.preventDefault()
    const move = (e: MouseEvent) => {
      const rect = u.over.getBoundingClientRect()
      let t = u.posToVal(e.clientX - rect.left, 'x')
      const min = u.scales.x.min ?? t
      const max = u.scales.x.max ?? t
      t = Math.max(min, Math.min(max, t))
      const cur = getRange()
      if (!cur) return
      onChange(which === 'from' ? { from: t, to: cur.to } : { from: cur.from, to: t })
    }
    const up = () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }

  return {
    hooks: {
      ready: (u) => {
        band = document.createElement('div')
        band.className = 'fleet-range-band'
        h0 = document.createElement('div')
        h1 = document.createElement('div')
        h0.className = h1.className = 'fleet-range-handle'
        u.over.appendChild(band)
        u.over.appendChild(h0)
        u.over.appendChild(h1)
        h0.addEventListener('mousedown', dragHandle(u, 'from'))
        h1.addEventListener('mousedown', dragHandle(u, 'to'))
        unregister = register(() => position(u))
        position(u)
      },
      setScale: (u) => position(u),
      setSize: (u) => position(u),
      destroy: () => {
        unregister?.()
        band?.remove(); h0?.remove(); h1?.remove()
        band = h0 = h1 = null
      },
    },
  }
}
```

- [ ] **Step 3: Rework the FleetGraph component state + two-click logic**

In `FleetGraph`, change the props and the click handling. Replace the `selectedTs` / `onSelectTs` props with `selectedRange` / `onSelectRange`. Keep a `rangeRef` mirroring the range for the plugin, and a `pendingStartRef` for the two-click protocol. The per-chart click handler (currently sets the slider on click) becomes:

```tsx
  // Two-click range: 1st click sets START (from==to), 2nd click sets END (ordered),
  // 3rd click starts a new range. Plain clicks only — drag still zooms.
  const handleRangeClick = useCallback((ts: number) => {
    if (pendingStartRef.current == null) {
      pendingStartRef.current = ts
      const r = { from: ts, to: ts }
      rangeRef.current = r
      positionersRef.current.forEach((fn) => fn())
      onSelectRange(r)
    } else {
      const from = pendingStartRef.current
      const r = { from: Math.min(from, ts), to: Math.max(from, ts) }
      pendingStartRef.current = null
      rangeRef.current = r
      positionersRef.current.forEach((fn) => fn())
      onSelectRange(r)
    }
  }, [onSelectRange])
```

In `PanelChart`, the `u.over` click handler calls `onRangeClick(ts)` (passed down) using the cursor's time `u.posToVal(...)` (NOT only snapped data points, so any x is selectable) — replace the old `onSliderChange` click body:

```tsx
    const onClick = (ev: MouseEvent) => {
      const rect = u.over.getBoundingClientRect()
      const t = u.posToVal(ev.clientX - rect.left, 'x')
      if (Number.isFinite(t)) onRangeClick(t)
    }
```

Pass `rangeRef` + `handleRangeClick` into `PanelChart` (replacing `sliderTimeRef`/`onSliderChange`), and swap the plugin: `rangePlugin(() => rangeRef.current, handleRangeClick, registerPositioner)` in place of `sliderPlugin(...)`. Seed `rangeRef.current` from `selectedRange` in an effect (as the old `selectedTs` effect did) and reposition.

Add the refs near the other refs:

```tsx
  const rangeRef = useRef<{ from: number; to: number } | null>(null)
  const pendingStartRef = useRef<number | null>(null)
```

- [ ] **Step 4: CSS for the band + handles**

Append to `frontend/src/styles/app.css`:

```css
.fleet-range-band { position: absolute; top: 0; bottom: 0; background: rgba(255,213,79,0.12); border-left: 1px solid var(--accent); border-right: 1px solid var(--accent); pointer-events: none; }
.fleet-range-handle { position: absolute; top: 0; bottom: 0; width: 9px; margin-left: -4px; cursor: ew-resize; }
.fleet-range-handle::before { content: ''; position: absolute; left: 4px; top: 0; bottom: 0; width: 1px; background: var(--accent); }
```

- [ ] **Step 5: Keep `FleetSection.tsx` compiling**

`FleetSection` (the thin wrapper) currently passes `selectedTs`/`onSelectTs`. Update it to own a `selectedRange` and pass `selectedRange`/`onSelectRange` to `FleetGraph` and `range` to the (now) `SnapshotPanel`:

```tsx
import { useState } from 'react'
import { FleetGraph } from './FleetGraph'
import { SnapshotPanel } from './SnapshotPanel'

export function FleetSection({ brId, reloadKey }: { brId: string; reloadKey?: number }) {
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  return (
    <div className="fleet-layout">
      <div className="fleet-main">
        <FleetGraph brId={brId} reloadKey={reloadKey} selectedRange={range} onSelectRange={setRange} />
      </div>
      {range != null && (
        <div className="fleet-side"><SnapshotPanel brId={brId} range={range} /></div>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Update FleetSection.test.tsx**

The existing graph tests (panels, toggles, kill legend, `fleet-chart-area`) still pass. If a test referenced the slider/`onSelectTs`, drop it. Confirm by running.

- [ ] **Step 7: Run frontend suite + typecheck**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS (FleetGraph via FleetSection, SnapshotPanel). tsc fails only in `BrDetailPage` until Task 9.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/FleetGraph.tsx frontend/src/components/FleetSection.tsx frontend/src/components/FleetSection.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): two-click snapshot range selector; ctrl-click-only zKill on kill markers"
```

---

### Task 8: FleetsPanel — reship badges, capsules already excluded

**Files:**
- Modify: `frontend/src/components/FleetsPanel.tsx`
- Modify: `frontend/src/components/FleetsPanel.test.tsx`
- Modify: `frontend/src/styles/app.css` (reship badge)

**Interfaces:**
- Consumes: `CompositionPilot.reship` (Task 5).

- [ ] **Step 1: Add a failing test**

Append to `frontend/src/components/FleetsPanel.test.tsx` (the fixture's pilots gain `reship`):

```tsx
  it('shows a reship badge on reshipped pilots in per-character', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1 },
                { ship_type_id: 11987, ship_name: 'Guardian', count: 1 }],
        pilots: [
          { character_id: 1, character_name: 'Talun', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: true, user_name: null },
          { character_id: 1, character_name: 'Talun', ship_type_id: 11987, ship_name: 'Guardian', lost: false, reship: true, user_name: null },
        ] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))
    expect(screen.getAllByText(/reship/i).length).toBeGreaterThanOrEqual(1)
  })
```

(Update the existing fixtures in this file to include `reship: false` on their pilots so types compile.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx`
Expected: FAIL — no reship badge rendered (and/or type errors on fixtures missing `reship`).

- [ ] **Step 3: Render the badge**

In `frontend/src/components/FleetsPanel.tsx`, in `PilotRow`, add the badge after the ship sub-label:

```tsx
function PilotRow({ p }: { p: CompositionPilot }) {
  return (
    <div className="comp-row">
      {shipIcon(p.ship_type_id, 18)}
      <span className="comp-name" title={p.character_name}>
        {p.character_name}{p.lost && <span className="comp-lost" title="lost ship"> ✗</span>}
      </span>
      <span className="dim comp-ship-sub">{p.ship_name}</span>
      {p.reship && <span className="comp-reship" title="reshipped during the battle">↻ reship</span>}
    </div>
  )
}
```

- [ ] **Step 4: CSS**

Append to `frontend/src/styles/app.css`:

```css
.comp-reship { flex: none; font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--accent); border: 1px solid var(--border); border-radius: 3px; padding: 0 0.2rem; }
```

- [ ] **Step 5: Run test + typecheck**

Run: `cd frontend && npx vitest run src/components/FleetsPanel.test.tsx && npx tsc --noEmit`
Expected: PASS; tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/FleetsPanel.tsx frontend/src/components/FleetsPanel.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): reship badge in FleetsPanel"
```

---

### Task 9: BrDetailPage layout — remove engagements, Summary→Sides, Snapshot rail, widen

**Files:**
- Modify: `frontend/src/views/BrDetailPage.tsx`
- Modify: `frontend/src/views/BrDetailPage.test.tsx`
- Modify: `frontend/src/styles/app.css` (`.page` width)

**Interfaces:**
- Consumes: `FleetGraph` (Task 7), `SnapshotPanel` (Task 6), `FleetsPanel` (Task 8), `BrDetail.systems` (Task 3).

Read the current `BrDetailPage.tsx` first. Changes:

- [ ] **Step 1: Update the test**

In `frontend/src/views/BrDetailPage.test.tsx`: the `vi.mock('../api')` must stub `api.snapshot` (not `api.contributions`); the contributions stub line (`{ at: 0, bucket_seconds: 5, rows: [] }`) becomes `vi.mocked(api.snapshot).mockResolvedValue({ from_ts: 0, to_ts: 0, rows: [] })`. Add a structure assertion and a removal assertion:

```tsx
  it('shows Summary then Sides, no Engagements/filter, Snapshot in the rail', async () => {
    renderBrDetailPage()
    await waitFor(() => expect(screen.getByTestId('br-detail-grid')).toBeInTheDocument())
    expect(screen.getByTestId('summary-section')).toBeInTheDocument()
    expect(screen.getByTestId('sides-section')).toBeInTheDocument()
    expect(screen.queryByText(/Filter sub-engagements/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Engagements$/i })).not.toBeInTheDocument()
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/BrDetailPage.test.tsx`
Expected: FAIL — `summary-section` testid absent / Engagements still present / `api.snapshot` not mocked.

- [ ] **Step 3: Restructure the page**

In `frontend/src/views/BrDetailPage.tsx`:

1. Imports: replace `MomentDetailPanel` with `SnapshotPanel`; remove `FilterBuilder` and `FightList` imports.
2. Remove fight-filter state + handlers (`filteredFights`, `fightFilterActive`, `fightFilterError`, `handleFightFilterApply`, `handleFightFilterClear`, `displayFights`) — no longer used.
3. Change the lifted state from `selectedTs`/`setSelectedTs` to:

```tsx
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
```

4. Give the existing stats panel `data-testid="summary-section"` and add the systems + a prominent ISK-destroyed line. In the stats `<div className="panel">`, prepend a System stat and keep the rest:

```tsx
      <div className="panel" data-testid="summary-section">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
          <div>
            <div className="stat-label">System</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {br.systems.length ? br.systems.join(', ') : '—'}
            </div>
          </div>
          <div>
            <div className="stat-label">ISK Destroyed</div>
            <div className="stat-value">{fmtIsk(br.our_isk_destroyed)}</div>
          </div>
          {/* keep existing Result / ISK Efficiency / ISK Killed / ISK Lost / Engagements / Source blocks */}
        </div>
      </div>
```

5. Delete the "Engagements" `<h2>`, the `<details>` filter block, the fight-filter count UI, and the `<FightList .../>`.
6. Replace the two-column grid block: left column = `FleetsPanel` + `FleetGraph`; right rail = `SnapshotPanel` only. Move Sides OUT of the rail to a full-width single-column section directly below the summary:

```tsx
      <section className="panel" data-testid="sides-section">
        <h2 style={{ margin: '0 0 0.75rem' }}>Sides</h2>
        {id && <SidesEditor brId={id} onChange={() => setSidesVersion((v) => v + 1)} />}
      </section>

      <div className="br-detail-grid" data-testid="br-detail-grid">
        <div className="br-col-main" data-testid="br-col-main">
          <section className="panel">
            {id && <FleetsPanel brId={id} reloadKey={sidesVersion} />}
          </section>
          <section data-testid="fleet-graph-section" className="panel">
            <h2 style={{ margin: '0 0 0.75rem' }}>Fleet Graph</h2>
            {id && (
              <FleetGraph brId={id} reloadKey={sidesVersion} selectedRange={range} onSelectRange={setRange} />
            )}
          </section>
        </div>
        <div className="br-col-side" data-testid="br-col-side">
          <section className="panel">
            <h3 style={{ margin: '0 0 0.5rem' }}>Snapshot</h3>
            {id && <SnapshotPanel brId={id} range={range} />}
          </section>
        </div>
      </div>
```

(Log Coverage section stays unchanged below.)

- [ ] **Step 4: Widen the page**

In `frontend/src/styles/app.css`, change `.page { max-width: 72rem; ... }` → `max-width: 80rem;`.

- [ ] **Step 5: Run tests + typecheck + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: all PASS; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/BrDetailPage.tsx frontend/src/views/BrDetailPage.test.tsx frontend/src/styles/app.css
git commit -m "feat(fe): BR detail layout — Summary+Sides, Snapshot rail, no engagements, wider page"
```

---

### Task 10: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `uv run pytest -q`
Expected: PASS (incl. `test_isk_value`, `test_br_systems`, `test_composition`, `test_e3_fleet_timeline`).

- [ ] **Step 2: Backend lint + types (no new findings vs baseline)**

Run: `uv run ruff check app tests && uv run mypy app`
Expected: no NEW errors in files this plan touched (pre-existing debt in `test_sides_api.py`, `sides_config.py`, `brs.py` may remain — do not fix unrelated debt; fix anything this plan introduced).

- [ ] **Step 3: Frontend suite + types + build**

Run: `cd frontend && npm test && npx tsc --noEmit && npm run build`
Expected: PASS.

- [ ] **Step 4: Real-data smoke (recommended)**

With the dev servers running and a real BR, verify: ISK destroyed is non-zero on the Summary; weapon icons resolve for the logged weapons (note any module names that fall back to a family icon); two-click range fills the Snapshot with `Name (Ship)` headers ordered busiest-first and quality tags; ctrl-click a kill marker opens zKill while plain clicks set the range; composition has no capsules and shows reship badges; the page has no Engagements section.

- [ ] **Step 5: Commit (if lint/type fixes were needed)**

```bash
git add -A && git commit -m "chore: lint/type fixes for BR detail refinements"
```

---

## Self-Review

**Spec coverage:**
- (A) layout — Task 9 (remove engagements/filter/list, Summary w/ system+ISK, Sides 1-col, Snapshot rail), Task 3 (`systems`), width in Task 9. ✓
- (B) kill-marker ctrl-click — Task 7. ✓
- (C) snapshot: range — Tasks 4 (backend) + 7 (two-click) + 6 (fetch); rename — Tasks 6/9; `Name (Ship)` — Tasks 4+6; ordering by effect count — Task 6; weapon icons — Task 4 (resolution) + Task 10 (coverage verification); quality — Tasks 4+6. ✓
- (D) capsules + reships — Task 5 (backend) + Task 8 (badge). ✓
- (E) ISK destroyed — Task 1 (/related/) + Task 2 (backfill). ✓
- Testing — per-task + Task 10. ✓

**Placeholder scan:** The Task 4 endpoint block shows a deliberately-wrong stub immediately followed by "Use this exact body:" + the correct full function — the implementer uses the second. Task 3's test helper notes confirming `SolarSystem`'s PK column name before writing (a real verify-step, not a placeholder). No TODO/TBD.

**Type consistency:** `Contribution`/`ContributionOut` gain `target_ship` + `quality` consistently (Tasks 4) and are consumed by `SnapshotPanel` (Task 6). `ContributionsResponse` is `{from_ts,to_ts,rows}` across api.ts (4), SnapshotPanel (6), and BrDetailPage test (9). `api.snapshot(brId, from, to)` matches its callers. `FleetGraph` props `selectedRange`/`onSelectRange` match between Tasks 7 and 9, and the `FleetSection` wrapper (7). `CompositionPilot.reship` is defined (5), serialized (5), typed in api.ts (5), and rendered (8). `ResolvedBr.values` + `persist_killmails(..., values=)` are produced in Task 1 and used by the pipeline there; Task 2's `backfill_killmail_values` matches its pipeline call.
