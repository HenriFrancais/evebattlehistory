# Hover Summary: Target-Only 3-Field Leaders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4-field side-agnostic `Leaders` shape with 3 side-aware fields — all by TARGET (receiver) — so the hover tooltip shows: (1) friendly pilot taking the most hostile damage, (2) hostile pilot taking the most friendly damage, (3) friendly pilot receiving the most reps.

**Architecture:** Build a `character_id → side` map inside `_compute_leaders` by querying KillmailAttacker + Killmail victim alliance_ids for the fight's killmails and running them through the existing `classify_entity` function (same function used for kill classification and BR outcome recomputation). The 4-field `Leaders` dataclass becomes 3-field; the schema, API mapping, TypeScript types, hover renderer, and tests all update in lockstep.

**Tech Stack:** Python 3.11 / SQLAlchemy async / Pydantic (backend); TypeScript / Vitest (frontend); pytest with in-memory SQLite fixture DB (backend tests); `uv run pytest` / `npm test` / `npx tsc --noEmit` / `uv run ruff check` / `uv run mypy app`.

## Global Constraints

- Branch: `feature/br-feedback-iteration`. All work goes on this branch.
- Backend test runner: `uv run pytest <path> -q` from `/home/matron/dev/nv-wh-fight-history/`
- Frontend test runner: `npm test -- <path>` from `/home/matron/dev/nv-wh-fight-history/frontend/`
- TypeScript check: `npx tsc --noEmit` from `frontend/`
- Lint: `uv run ruff check app tests` from project root
- Type-check: `uv run mypy app` from project root
- TDD: write failing test first, run it to confirm RED, then implement, confirm GREEN.
- Commit after each task; message ends with blank line then `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- YAGNI: do not add fields or behaviour not listed here.
- Unknown-side characters are treated as **HOSTILE** (avoids ever mislabelling a non-NV pilot as friendly). Document this in a code comment.
- The `fleet_timeline()` caller already passes `our_alliance_ids` + `our_corp_ids` from `get_app_config()`; `_compute_leaders` must accept and use these. The existing `settings` parameter on `_compute_leaders` is retained for name resolution.

---

## File Map

| File | Change |
|------|--------|
| `app/analytics/fleet.py` | `Leaders` dataclass: 4 → 3 fields; `_compute_leaders`: build side map, split accumulators by side |
| `app/api/schemas.py` | `LeadersOut`: 4 → 3 fields |
| `app/api/fleet.py` | `get_fleet_timeline`: update `LeadersOut(...)` constructor call |
| `tests/test_e3_fleet_timeline.py` | Update existing leaders tests (remove references to old fields); add new side-aware test |
| `frontend/src/api.ts` | `Leaders` interface: 4 → 3 fields |
| `frontend/src/hoverSummary.ts` | Rewrite `allNull` + `renderHoverSummary`; drop side-totals, drop dealer/repper entries |
| `frontend/src/hoverSummary.test.ts` | New fixture shape; updated assertions for 3 labeled entries; drop side-totals test |

---

## Task 1 — Backend: RED test for side-aware leaders

**Files:**
- Modify: `tests/test_e3_fleet_timeline.py`

**Interfaces:**
- Consumes: `fleet_timeline(session, br_id, our_alliance_ids=..., settings=...)` (existing signature)
- Produces: `tl.leaders[idx]` with `.top_friendly_dmg_taken`, `.top_hostile_dmg_taken`, `.top_friendly_rep_recv` — all `LeaderEntry | None`

- [ ] **Step 1: Write the two failing tests** — open `tests/test_e3_fleet_timeline.py`, add AFTER the existing `test_leaders_empty_for_no_logs` block (line ~801):

```python
# ---------------------------------------------------------------------------
# Task 1 (side-aware): per-bucket leaders split by target's side
# ---------------------------------------------------------------------------

# Alliance IDs used in side-aware tests:
_FRIENDLY_ALLI = 99006113   # NV baseline — always friendly
_HOSTILE_ALLI  = 88888888   # not in baseline → hostile


async def test_side_aware_leaders_friendly_dmg_taken(db_session_maker) -> None:
    """top_friendly_dmg_taken = FRIENDLY char with max incoming damage.
    top_hostile_dmg_taken = HOSTILE char with max incoming damage.
    top_friendly_rep_recv = FRIENDLY char with max incoming reps.
    Unknown-side chars are treated as HOSTILE."""
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character, Killmail, KillmailAttacker

    # Four characters:
    #   CHAR_F1 (friendly, alliance_id=99006113) — more dmg taken than CHAR_F2
    #   CHAR_F2 (friendly, alliance_id=99006113) — less dmg taken; more reps recv than CHAR_F1
    #   CHAR_H1 (hostile,  alliance_id=88888888) — highest hostile dmg taken
    #   CHAR_H2 (hostile,  alliance_id=88888888) — less dmg taken
    CHAR_F1 = 3100000001
    CHAR_F2 = 3100000002
    CHAR_H1 = 3200000001
    CHAR_H2 = 3200000002

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)

        # Create Character rows so names resolve
        for cid, name in (
            (CHAR_F1, "FriendlyOne"), (CHAR_F2, "FriendlyTwo"),
            (CHAR_H1, "HostileOne"), (CHAR_H2, "HostileTwo"),
        ):
            await session.merge(
                Character(character_id=cid, name=name,
                          last_seen_at=dt.datetime.now(dt.UTC))
            )
        await session.flush()

        # Killmail to establish alliance membership via KillmailAttacker rows.
        # Use unique killmail_id values that won't collide with fight's own KMs.
        km_base = 9_000_000
        for i, (cid, alli) in enumerate(
            [(CHAR_F1, _FRIENDLY_ALLI), (CHAR_F2, _FRIENDLY_ALLI),
             (CHAR_H1, _HOSTILE_ALLI), (CHAR_H2, _HOSTILE_ALLI)]
        ):
            km_id = km_base + i
            # Minimal Killmail row needed for FightKill FK
            session.add(Killmail(
                killmail_id=km_id,
                killmail_time=FIGHT_START,
                solar_system_id=31002222,
                victim_character_id=None,
                victim_ship_type_id=_SHIP_TYPE_ID,
                total_value=0.0,
                npc_kill=False,
                solo_kill=False,
            ))
            await session.flush()
            from app.db.models import FightKill
            session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
            await session.flush()
            # Attacker row that carries alliance_id → side classification
            session.add(KillmailAttacker(
                killmail_id=km_id,
                character_id=cid,
                corporation_id=None,
                alliance_id=alli,
                ship_type_id=_SHIP_TYPE_ID,
                weapon_type_id=None,
                damage_done=0,
                final_blow=False,
                security_status=0.0,
            ))
        await session.flush()

        # Log buckets: incoming damage for both sides; incoming reps for friendly
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "damage", "in", 500.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F2, BUCKET_TS_1, "damage", "in", 200.0, 1)
        await _insert_bucket(session, fight_id, CHAR_H1, BUCKET_TS_1, "damage", "in", 800.0, 1)
        await _insert_bucket(session, fight_id, CHAR_H2, BUCKET_TS_1, "damage", "in", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F2, BUCKET_TS_1, "rep_armor", "in", 300.0, 1)
        await _insert_bucket(session, fight_id, CHAR_F1, BUCKET_TS_1, "rep_armor", "in", 100.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(
            session, br_id,
            our_alliance_ids=[_FRIENDLY_ALLI],
            settings=get_settings(),
        )

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]

    # Friendly char with most incoming damage
    assert ld.top_friendly_dmg_taken is not None
    assert ld.top_friendly_dmg_taken.name == "FriendlyOne"
    assert ld.top_friendly_dmg_taken.amount == pytest.approx(500.0)

    # Hostile char with most incoming damage
    assert ld.top_hostile_dmg_taken is not None
    assert ld.top_hostile_dmg_taken.name == "HostileOne"
    assert ld.top_hostile_dmg_taken.amount == pytest.approx(800.0)

    # Friendly char with most incoming reps
    assert ld.top_friendly_rep_recv is not None
    assert ld.top_friendly_rep_recv.name == "FriendlyTwo"
    assert ld.top_friendly_rep_recv.amount == pytest.approx(300.0)


async def test_side_aware_leaders_null_when_no_friendly(db_session_maker) -> None:
    """When only hostile chars have log data, top_friendly_* fields are None."""
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character, FightKill, Killmail, KillmailAttacker

    CHAR_H1 = 3300000001

    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await session.merge(
            Character(character_id=CHAR_H1, name="HostileOnly",
                      last_seen_at=dt.datetime.now(dt.UTC))
        )
        await session.flush()

        km_id = 9_100_000
        session.add(Killmail(
            killmail_id=km_id, killmail_time=FIGHT_START,
            solar_system_id=31002222, victim_character_id=None,
            victim_ship_type_id=_SHIP_TYPE_ID, total_value=0.0,
            npc_kill=False, solo_kill=False,
        ))
        await session.flush()
        session.add(FightKill(fight_id=fight_id, killmail_id=km_id, side_idx=0))
        await session.flush()
        session.add(KillmailAttacker(
            killmail_id=km_id, character_id=CHAR_H1, corporation_id=None,
            alliance_id=_HOSTILE_ALLI, ship_type_id=_SHIP_TYPE_ID,
            weapon_type_id=None, damage_done=0, final_blow=False, security_status=0.0,
        ))
        await session.flush()

        await _insert_bucket(session, fight_id, CHAR_H1, BUCKET_TS_1, "damage", "in", 400.0, 1)
        await session.commit()

    async with db_session_maker() as session:
        tl = await fleet_timeline(
            session, br_id,
            our_alliance_ids=[_FRIENDLY_ALLI],
            settings=get_settings(),
        )

    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]
    assert ld.top_friendly_dmg_taken is None
    assert ld.top_hostile_dmg_taken is not None
    assert ld.top_hostile_dmg_taken.name == "HostileOnly"
    assert ld.top_friendly_rep_recv is None
```

- [ ] **Step 2: Run to confirm RED**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_e3_fleet_timeline.py::test_side_aware_leaders_friendly_dmg_taken tests/test_e3_fleet_timeline.py::test_side_aware_leaders_null_when_no_friendly -q 2>&1 | tail -20
```

Expected: FAIL — `AttributeError: 'Leaders' object has no attribute 'top_friendly_dmg_taken'`

---

## Task 2 — Backend: implement side-aware `Leaders` + `_compute_leaders`

**Files:**
- Modify: `app/analytics/fleet.py` lines 346–558

**Interfaces:**
- Produces: `Leaders(top_friendly_dmg_taken, top_hostile_dmg_taken, top_friendly_rep_recv)` — all `LeaderEntry | None`
- `_compute_leaders(session, fight_ids, x, x_index, settings, friendly_alliances, friendly_corps)` — two new parameters

- [ ] **Step 1: Replace `Leaders` dataclass** (lines 346–353 in `fleet.py`)

Old:
```python
@dataclass
class Leaders:
    """Per-bucket leaders across the four tracked metrics."""

    top_dmg_taken: LeaderEntry | None
    top_rep_recv: LeaderEntry | None
    top_dmg_dealt: LeaderEntry | None
    top_rep_done: LeaderEntry | None
```

New:
```python
@dataclass
class Leaders:
    """Per-bucket leaders, split by the TARGET's side.

    'friendly' = target's alliance_id ∈ friendly_alliances (or corp_id ∈ friendly_corps).
    Characters whose side cannot be determined are treated as HOSTILE to avoid
    mislabelling non-NV pilots as friendly.
    """

    top_friendly_dmg_taken: LeaderEntry | None
    """Friendly character receiving the most incoming damage this bucket."""
    top_hostile_dmg_taken: LeaderEntry | None
    """Hostile (or unknown-side) character receiving the most incoming damage this bucket."""
    top_friendly_rep_recv: LeaderEntry | None
    """Friendly character receiving the most incoming reps this bucket."""
```

- [ ] **Step 2: Add side-map helper and update `_compute_leaders` signature** — replace the entire `_compute_leaders` function (lines 461–558) with:

```python
async def _build_char_side_map(
    session: AsyncSession,
    fight_ids: list[int],
    friendly_alliances: set[int],
    friendly_corps: set[int],
) -> dict[int, str]:
    """Return character_id → 'friendly' | 'hostile' from killmail participants.

    Victims are checked first (authoritative hull source mirrors _resolve_char_ships).
    Attacker rows supplement missing entries.  Characters with no alliance/corp info,
    or whose alliance/corp is not in the friendly sets, are classified 'hostile' —
    this avoids ever mislabelling a non-NV pilot as friendly.
    """
    km_ids: list[int] = list(
        (
            await session.execute(
                select(FightKill.killmail_id).where(FightKill.fight_id.in_(fight_ids))
            )
        ).scalars()
    )
    if not km_ids:
        return {}

    char_side: dict[int, str] = {}

    # Victims
    for char_id, alli_id, corp_id in (
        await session.execute(
            select(
                Killmail.victim_character_id,
                Killmail.victim_alliance_id,
                Killmail.victim_corporation_id,
            ).where(Killmail.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        side = (
            "friendly"
            if (alli_id is not None and alli_id in friendly_alliances)
            or (corp_id is not None and corp_id in friendly_corps)
            else "hostile"
        )
        char_side[char_id] = side

    # Attackers (fill gaps — victim entry takes precedence via setdefault)
    for char_id, alli_id, corp_id in (
        await session.execute(
            select(
                KillmailAttacker.character_id,
                KillmailAttacker.alliance_id,
                KillmailAttacker.corporation_id,
            ).where(KillmailAttacker.killmail_id.in_(km_ids))
        )
    ).all():
        if char_id is None:
            continue
        side = (
            "friendly"
            if (alli_id is not None and alli_id in friendly_alliances)
            or (corp_id is not None and corp_id in friendly_corps)
            else "hostile"
        )
        char_side.setdefault(char_id, side)

    return char_side


async def _compute_leaders(
    session: AsyncSession,
    fight_ids: list[int],
    x: list[int],
    x_index: dict[int, int],
    settings: Settings,
    friendly_alliances: set[int] | None = None,
    friendly_corps: set[int] | None = None,
) -> list[Leaders]:
    """Return per-bucket Leaders aligned index-for-index to *x*.

    Returns [] (not a list of Leaders) when no log buckets exist.

    Metric classification (by TARGET side):
      FRIENDLY target, damage / in  → top_friendly_dmg_taken
      HOSTILE  target, damage / in  → top_hostile_dmg_taken
      FRIENDLY target, rep* / in    → top_friendly_rep_recv

    Characters not found in the killmail side map are treated as HOSTILE.
    """
    if not x:
        return []

    eff_friendly_alliances: set[int] = friendly_alliances or set()
    eff_friendly_corps: set[int] = friendly_corps or set()

    # Build character → side map from killmail participants.
    char_side_map = await _build_char_side_map(
        session, fight_ids, eff_friendly_alliances, eff_friendly_corps
    )

    # Fetch per-(bucket_ts, character_id, effect_type, direction) aggregates.
    rows = (
        await session.execute(
            select(
                LogEventBucket.bucket_ts,
                LogEventBucket.character_id,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
                func.sum(func.abs(LogEventBucket.sum_amount)).label("total"),
            )
            .where(
                LogEventBucket.fight_id.in_(fight_ids),
                LogEventBucket.effect_type.in_(_AMOUNT_EFFECTS),
                LogEventBucket.direction.in_(_DIRECTION_ORDER),
            )
            .group_by(
                LogEventBucket.bucket_ts,
                LogEventBucket.character_id,
                LogEventBucket.effect_type,
                LogEventBucket.direction,
            )
        )
    ).all()

    if not rows:
        return []

    char_ids: set[int] = {char_id for _, char_id, _, _, _ in rows}
    char_names = await _resolve_char_names(session, settings, char_ids)
    char_ships = await _resolve_char_ships(session, fight_ids)

    # Three side-aware accumulators per bucket index:
    #   "friendly_dmg_taken" | "hostile_dmg_taken" | "friendly_rep_recv"
    BucketAcc = dict[str, dict[int, float]]
    bucket_acc: dict[int, BucketAcc] = {}

    for bucket_ts, char_id, effect_type, direction, total in rows:
        idx = x_index.get(_epoch(bucket_ts))
        if idx is None:
            continue
        amount = float(total or 0.0)

        # Only incoming effects matter; outgoing is dropped (we track receivers).
        if direction != "in":
            continue

        # Determine target's side; unknown → hostile (safe default).
        side = char_side_map.get(char_id, "hostile")

        if effect_type == "damage":
            metric = "friendly_dmg_taken" if side == "friendly" else "hostile_dmg_taken"
        elif effect_type.startswith("rep"):
            if side != "friendly":
                continue  # no hostile rep recv entry
            metric = "friendly_rep_recv"
        else:
            continue

        acc = bucket_acc.setdefault(idx, {})
        per_char = acc.setdefault(metric, {})
        per_char[char_id] = per_char.get(char_id, 0.0) + amount

    def _best(per_char: dict[int, float] | None) -> LeaderEntry | None:
        if not per_char:
            return None
        best_id, best_amt = max(per_char.items(), key=lambda kv: kv[1])
        name = char_names.get(best_id) or f"Char {best_id}"
        return LeaderEntry(name=name, ship=char_ships.get(best_id), amount=best_amt)

    leaders: list[Leaders] = []
    for i in range(len(x)):
        acc = bucket_acc.get(i, {})
        leaders.append(
            Leaders(
                top_friendly_dmg_taken=_best(acc.get("friendly_dmg_taken")),
                top_hostile_dmg_taken=_best(acc.get("hostile_dmg_taken")),
                top_friendly_rep_recv=_best(acc.get("friendly_rep_recv")),
            )
        )
    return leaders
```

- [ ] **Step 3: Update `fleet_timeline` call to `_compute_leaders`** — in `fleet_timeline()` (line ~757), change:

Old:
```python
    leaders = await _compute_leaders(
        session, fight_ids, x, x_index, settings or get_settings()
    )
```

New:
```python
    leaders = await _compute_leaders(
        session, fight_ids, x, x_index, settings or get_settings(),
        friendly_alliances=friendly_alliances,
        friendly_corps=friendly_corps,
    )
```

(Note: `friendly_alliances` and `friendly_corps` are already in scope as `set(our_alliance_ids)` and `set(our_corp_ids)` — but they aren't yet assigned to locals. Check the actual variable names in `fleet_timeline`; they are `friendly_alliances = set(our_alliance_ids)` and `friendly_corps = set(our_corp_ids)` set at lines 582–583. Pass those exact names.)

- [ ] **Step 4: Run the two new tests (expect GREEN)**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_e3_fleet_timeline.py::test_side_aware_leaders_friendly_dmg_taken tests/test_e3_fleet_timeline.py::test_side_aware_leaders_null_when_no_friendly -q 2>&1 | tail -20
```

Expected: both PASS.

---

## Task 3 — Backend: update schemas, API mapping, and fix existing tests

**Files:**
- Modify: `app/api/schemas.py` (lines 389–395)
- Modify: `app/api/fleet.py` (lines 108–114)
- Modify: `tests/test_e3_fleet_timeline.py` — update old tests to 3-field shape

**Interfaces:**
- `LeadersOut` now has: `top_friendly_dmg_taken`, `top_hostile_dmg_taken`, `top_friendly_rep_recv` (all `LeaderEntryOut | None`)

- [ ] **Step 1: Update `LeadersOut` in `app/api/schemas.py`**

Old (lines 389–395):
```python
class LeadersOut(BaseModel):
    """Per-bucket leaders across the four tracked metrics."""

    top_dmg_taken: LeaderEntryOut | None
    top_rep_recv: LeaderEntryOut | None
    top_dmg_dealt: LeaderEntryOut | None
    top_rep_done: LeaderEntryOut | None
```

New:
```python
class LeadersOut(BaseModel):
    """Per-bucket leaders split by the target's side (3 fields)."""

    top_friendly_dmg_taken: LeaderEntryOut | None
    top_hostile_dmg_taken: LeaderEntryOut | None
    top_friendly_rep_recv: LeaderEntryOut | None
```

- [ ] **Step 2: Update `LeadersOut(...)` constructor in `app/api/fleet.py`** (lines 109–113)

Old:
```python
            LeadersOut(
                top_dmg_taken=_leader_out(ld.top_dmg_taken),
                top_rep_recv=_leader_out(ld.top_rep_recv),
                top_dmg_dealt=_leader_out(ld.top_dmg_dealt),
                top_rep_done=_leader_out(ld.top_rep_done),
            )
```

New:
```python
            LeadersOut(
                top_friendly_dmg_taken=_leader_out(ld.top_friendly_dmg_taken),
                top_hostile_dmg_taken=_leader_out(ld.top_hostile_dmg_taken),
                top_friendly_rep_recv=_leader_out(ld.top_friendly_rep_recv),
            )
```

- [ ] **Step 3: Fix the four existing leaders tests in `tests/test_e3_fleet_timeline.py`** that reference the old field names:

  **`test_leaders_per_bucket_picks_max_character`** (lines 737–769): This test verifies max-picking logic, but the setup has no alliance data, so side classification can't be trusted. Replace the four assertions with side-agnostic checks that the 3 new fields are present. The test now verifies the max-selection still happens (by checking that the char with 300 dmg_in beats the one with 100 dmg_in regardless of side), so seed CHAR_B with a friendly alliance to make `top_friendly_dmg_taken` meaningful — OR just check at least one field is non-null. The simplest correct approach: update the test to reflect what's now computed. Since no alliance data exists, both CHAR_A and CHAR_B have unknown side → both "hostile". So `top_friendly_dmg_taken = None`, `top_hostile_dmg_taken = max(100, 300) = Bob (300)`, `top_friendly_rep_recv = None`.

  Replace the assertion block (lines 764–769) with:
  ```python
      idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
      ld = tl.leaders[idx]
      # No killmail alliance data → both chars are unknown-side (treated as hostile).
      assert ld.top_friendly_dmg_taken is None
      assert ld.top_hostile_dmg_taken is not None
      assert ld.top_hostile_dmg_taken.name == "Bob"
      assert ld.top_hostile_dmg_taken.amount == pytest.approx(300.0)
      assert ld.top_friendly_rep_recv is None
  ```

  **`test_leaders_aligned_to_x_and_null_when_absent`** (lines 772–787): Replace lines 785–787 with:
  ```python
      assert len(tl.leaders) == len(tl.x)
      ld = tl.leaders[tl.x.index(int(BUCKET_TS_1.timestamp()))]
      # damage:out bucket only — all 3 incoming-damage/rep fields are None
      assert ld.top_friendly_dmg_taken is None
      assert ld.top_hostile_dmg_taken is None
      assert ld.top_friendly_rep_recv is None
  ```

  **`test_api_fleet_timeline_includes_leaders`** (lines 809–862): The bucket is `damage:in` for CHAR_A (unknown alliance → hostile). Update the assertions (lines 854–858):
  ```python
      assert ld["top_friendly_dmg_taken"] is None
      assert ld["top_hostile_dmg_taken"] is not None
      assert ld["top_hostile_dmg_taken"]["name"] == "Alice"
      assert ld["top_hostile_dmg_taken"]["amount"] == pytest.approx(400.0)
      assert ld["top_friendly_rep_recv"] is None
  ```

  Also update the docstring of `test_api_fleet_timeline_includes_leaders` (line 811–812):
  ```python
      """GET /api/brs/{br_id}/fleet-timeline has leaders[] aligned to x[],
      with top_hostile_dmg_taken populated (CHAR_A unknown-side → hostile)."""
  ```

- [ ] **Step 4: Run the full backend test file (expect all GREEN)**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest tests/test_e3_fleet_timeline.py -q 2>&1 | tail -30
```

Expected: all tests PASS (no failures, no errors).

- [ ] **Step 5: Run ruff + mypy on touched files**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run ruff check app/analytics/fleet.py app/api/schemas.py app/api/fleet.py && uv run mypy app/analytics/fleet.py app/api/schemas.py app/api/fleet.py 2>&1 | tail -20
```

Expected: no errors.

- [ ] **Step 6: Run full backend suite**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add app/analytics/fleet.py app/api/schemas.py app/api/fleet.py tests/test_e3_fleet_timeline.py && git commit -m "$(cat <<'EOF'
feat(leaders): side-aware 3-field Leaders — friendly/hostile dmg + friendly reps

Replace 4-field Leaders (top_dmg_taken/top_rep_recv/top_dmg_dealt/top_rep_done)
with 3 side-aware fields keyed by the TARGET's side:
  - top_friendly_dmg_taken: friendly pilot receiving most hostile damage
  - top_hostile_dmg_taken:  hostile pilot receiving most friendly damage
  - top_friendly_rep_recv:  friendly pilot receiving most reps

Side classification uses classify_entity logic (alliance_id lookup against
our_alliance_ids+baseline) via a new _build_char_side_map helper that reads
KillmailAttacker + Killmail victim rows. Unknown-side chars → HOSTILE.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — Frontend: RED test for updated hover summary

**Files:**
- Modify: `frontend/src/hoverSummary.test.ts`

**Interfaces:**
- Consumes: `Leaders` from `./api` with fields `top_friendly_dmg_taken`, `top_hostile_dmg_taken`, `top_friendly_rep_recv`
- `renderHoverSummary(view, leaders, idx)` — same signature

- [ ] **Step 1: Rewrite the test file** (`frontend/src/hoverSummary.test.ts`) — completely replace its content:

```typescript
import { describe, expect, it } from 'vitest'
import type { FleetTimeline, Leaders } from './api'
import { toFleetView } from './fleet'
import { renderHoverSummary } from './hoverSummary'

function mk(effect_type: string, direction: string, values: (number | null)[]) {
  const metric = ['scram', 'disrupt', 'jam'].includes(effect_type) ? 'count' : 'amount'
  return { key: `${effect_type}:${direction}`, effect_type, direction, metric, values }
}

const leadersPopulated: Leaders = {
  top_friendly_dmg_taken: { name: 'Bob<evil>', ship: 'Tengu', amount: 12000 },
  top_hostile_dmg_taken: { name: 'EnemyAce', ship: 'Loki', amount: 9000 },
  top_friendly_rep_recv: { name: 'Alice', ship: 'Scimitar', amount: 8500 },
}

const leadersAllNull: Leaders = {
  top_friendly_dmg_taken: null,
  top_hostile_dmg_taken: null,
  top_friendly_rep_recv: null,
}

const fleet: FleetTimeline = {
  x: [1000, 1005],
  series: [
    mk('damage', 'out', [500, 300]),
    mk('damage', 'in', [200, 100]),
    mk('rep_armor', 'in', [150, 75]),
    mk('rep_shield', 'in', [50, 25]),
  ],
  kills: [],
  fights: [],
  bucket_seconds: 5,
  t_start: 1000,
  t_end: 1005,
  leaders: [],
}

describe('renderHoverSummary', () => {
  const view = toFleetView(fleet, { smooth: false })
  const leaders = [leadersPopulated, leadersAllNull]

  it('bucket 0 — friendly dmg target has prominent class and escaped name', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('hover-tip-top')
    expect(html).toContain('Bob&lt;evil&gt;')
    expect(html).toContain('Friendly taking most damage')
  })

  it('bucket 0 — hostile dmg target appears with label', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('EnemyAce')
    expect(html).toContain('Hostile taking most damage')
  })

  it('bucket 0 — friendly rep target appears with label', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Alice')
    expect(html).toContain('Friendly receiving most reps')
  })

  it('bucket 0 — amounts formatted with fmtCompact', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('12.0k')  // 12000 → "12.0k"
    expect(html).toContain('9.0k')   // 9000  → "9.0k"
    expect(html).toContain('8.5k')   // 8500  → "8.5k"
  })

  it('bucket 0 — ship names appear in output', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Tengu')
    expect(html).toContain('Loki')
    expect(html).toContain('Scimitar')
  })

  it('bucket 0 — does NOT contain old dealer/repper labels', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).not.toContain('Top dmg:')
    expect(html).not.toContain('Top rep:')
    expect(html).not.toContain('Dmg recv:')
    expect(html).not.toContain('Rep recv:')
  })

  it('bucket 0 — does NOT contain side-total lines', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).not.toContain('DPS out:')
    expect(html).not.toContain('Dmg in:')
    expect(html).not.toContain('Rep in:')
  })

  it('bucket 1 (all-null) — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 1)
    expect(html).toContain('no log data')
  })

  it('out-of-range idx — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 99)
    expect(html).toContain('no log data')
  })
})
```

- [ ] **Step 2: Run test to confirm RED**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- src/hoverSummary.test.ts 2>&1 | tail -30
```

Expected: FAIL — TypeScript compile error (`Leaders` has wrong shape) and/or assertion failures on new label strings.

---

## Task 5 — Frontend: update `Leaders` type and `renderHoverSummary`

**Files:**
- Modify: `frontend/src/api.ts` (lines 333–338)
- Modify: `frontend/src/hoverSummary.ts` (full rewrite)

**Interfaces:**
- `Leaders.top_friendly_dmg_taken: LeaderEntry | null`
- `Leaders.top_hostile_dmg_taken: LeaderEntry | null`
- `Leaders.top_friendly_rep_recv: LeaderEntry | null`
- `renderHoverSummary(view: FleetView, leaders: Leaders[], idx: number): string` — same signature, `view` parameter retained for signature compatibility but no longer used for side-totals

- [ ] **Step 1: Update `Leaders` interface in `frontend/src/api.ts`** — replace lines 333–338:

Old:
```typescript
export interface Leaders {
  top_dmg_taken: LeaderEntry | null
  top_rep_recv: LeaderEntry | null
  top_dmg_dealt: LeaderEntry | null
  top_rep_done: LeaderEntry | null
}
```

New:
```typescript
export interface Leaders {
  top_friendly_dmg_taken: LeaderEntry | null
  top_hostile_dmg_taken: LeaderEntry | null
  top_friendly_rep_recv: LeaderEntry | null
}
```

- [ ] **Step 2: Rewrite `frontend/src/hoverSummary.ts`** — full replacement:

```typescript
// Pure HTML builder for the cursor hover-summary tooltip.
// No uPlot import — unit-tested in jsdom.
//
// Shows three side-aware per-bucket leaders (all by TARGET/receiver):
//   1. Friendly target receiving the most hostile damage (top_friendly_dmg_taken)
//   2. Hostile target receiving the most friendly damage (top_hostile_dmg_taken)
//   3. Friendly target receiving the most friendly reps  (top_friendly_rep_recv)
//
// The `view` parameter is retained for API compatibility with the FleetGraph
// plugin wiring, but is no longer used for side-total computation.

import type { Leaders } from './api'
import type { FleetView } from './fleet'
import { fmtCompact } from './format'

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function allNull(l: Leaders): boolean {
  return (
    l.top_friendly_dmg_taken == null &&
    l.top_hostile_dmg_taken == null &&
    l.top_friendly_rep_recv == null
  )
}

function leaderLine(label: string, e: { name: string; ship: string | null; amount: number }): string {
  return (
    `<div class="hover-tip-top">` +
    `<span class="hover-tip-label">${label}:</span> ` +
    `<strong>${esc(e.name)}</strong>` +
    (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
    ` <span class="hover-tip-amount">${fmtCompact(e.amount)}</span>` +
    `</div>`
  )
}

/**
 * Render an HTML string for the hover tooltip at `idx`.
 *
 * Shows only the 3 side-aware receiver leaders:
 *   - "Friendly taking most damage: <name> (<ship>) <amount>"
 *   - "Hostile taking most damage: <name> (<ship>) <amount>"
 *   - "Friendly receiving most reps: <name> (<ship>) <amount>"
 *
 * Returns a "no log data" line when the bucket has no leader data.
 * The `view` parameter is unused but kept for FleetGraph plugin compatibility.
 */
export function renderHoverSummary(_view: FleetView, leaders: Leaders[], idx: number): string {
  const entry = leaders[idx]
  if (!entry || allNull(entry)) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  let html = ''
  if (entry.top_friendly_dmg_taken) {
    html += leaderLine('Friendly taking most damage', entry.top_friendly_dmg_taken)
  }
  if (entry.top_hostile_dmg_taken) {
    html += leaderLine('Hostile taking most damage', entry.top_hostile_dmg_taken)
  }
  if (entry.top_friendly_rep_recv) {
    html += leaderLine('Friendly receiving most reps', entry.top_friendly_rep_recv)
  }

  // If all were non-null in entry but rendered nothing (defensive), show fallback.
  if (!html) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  return html
}
```

- [ ] **Step 3: Run the hover test (expect GREEN)**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- src/hoverSummary.test.ts 2>&1 | tail -30
```

Expected: all tests PASS.

- [ ] **Step 4: Run tsc**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit 2>&1 | head -40
```

Expected: no errors. If `FleetGraph.tsx` or any other file passes `view` to `renderHoverSummary`, that still compiles because `_view` is the renamed parameter — the call site is unchanged.

- [ ] **Step 5: Run full frontend test suite**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/src/api.ts frontend/src/hoverSummary.ts frontend/src/hoverSummary.test.ts && git commit -m "$(cat <<'EOF'
feat(frontend): 3-field side-aware hover tooltip — target receivers only

Update Leaders TypeScript interface to 3 fields matching the backend
(top_friendly_dmg_taken, top_hostile_dmg_taken, top_friendly_rep_recv).
Rewrite renderHoverSummary: drop side-total lines and dealer/repper entries;
render only the 3 labeled receiver entries with clear friendly/hostile labels.
Update hoverSummary.test.ts fixture and assertions accordingly.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — Final verification + real-data check + report

**Files:**
- Create: `/home/matron/dev/nv-wh-fight-history/.superpowers/sdd/hover-targets-report.md`

- [ ] **Step 1: Run the full backend suite**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 2: Run ruff + mypy**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run ruff check app tests && uv run mypy app 2>&1 | tail -20
```

Expected: clean.

- [ ] **Step 3: Run full frontend suite + tsc**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test 2>&1 | tail -10 && npx tsc --noEmit 2>&1 | head -10
```

Expected: all pass / no type errors.

- [ ] **Step 4: Real-data check against dev DB**

If the FastAPI dev server is not running, start it:
```bash
cd /home/matron/dev/nv-wh-fight-history && uv run uvicorn app.main:app --reload --port 8765 &
sleep 2
```

Then query (fight 8 per MEMORY.md is a 3v1 with logs):
```bash
# First list BRs to find a br_id that maps to fight 8
curl -s -H "Authorization: Bearer dev-token-change-me" \
  http://localhost:8765/api/brs | python3 -c "import sys,json; brs=json.load(sys.stdin)['brs']; [print(b['br_id'], b.get('title','')) for b in brs[:5]]"

# Then query fleet-timeline for the first br_id returned
BR_ID="<id from above>"
curl -s -H "Authorization: Bearer dev-token-change-me" \
  "http://localhost:8765/api/brs/$BR_ID/fleet-timeline" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
leaders = d.get('leaders', [])
print(f'x buckets: {len(d[\"x\"])}, leaders: {len(leaders)}')
for i, ld in enumerate(leaders[:3]):
    print(f'bucket {i}:', json.dumps(ld, indent=2))
"
```

Record the output: confirm `top_friendly_dmg_taken`, `top_hostile_dmg_taken`, `top_friendly_rep_recv` fields appear with pilot names.

Kill the background server:
```bash
kill %1 2>/dev/null; true
```

- [ ] **Step 5: Write the report**

Create `/home/matron/dev/nv-wh-fight-history/.superpowers/sdd/hover-targets-report.md`:

```markdown
# Hover Summary: Target-Only 3-Field Leaders — Implementation Report

## Side Classification Mechanism
Reused `classify_entity` logic from `app/analytics/sides_config.py` via a new
`_build_char_side_map` helper in `app/analytics/fleet.py`.  Queries
`KillmailAttacker.alliance_id` + `Killmail.victim_alliance_id` for the fight's
killmails; a character is **friendly** iff their `alliance_id ∈ our_alliance_ids`
(config baseline, always includes NV 99006113/99009324/99014963) or
`corp_id ∈ our_corp_ids`.  Unknown-side characters default to **hostile**
(avoids mislabelling non-NV pilots as friendly).

## 3-Field Leaders (backend + frontend)
| Field | Meaning |
|-------|---------|
| `top_friendly_dmg_taken` | Friendly char with max incoming damage in this bucket |
| `top_hostile_dmg_taken`  | Hostile (or unknown-side) char with max incoming damage |
| `top_friendly_rep_recv`  | Friendly char with max incoming reps |

## Files Changed
- `app/analytics/fleet.py` — `Leaders` dataclass (4→3 fields), new `_build_char_side_map`, updated `_compute_leaders`
- `app/api/schemas.py` — `LeadersOut` (4→3 fields)
- `app/api/fleet.py` — updated `LeadersOut(...)` constructor
- `tests/test_e3_fleet_timeline.py` — 2 new side-aware tests; 3 old tests updated
- `frontend/src/api.ts` — `Leaders` interface (4→3 fields)
- `frontend/src/hoverSummary.ts` — rewritten renderer (no totals, 3 labeled lines)
- `frontend/src/hoverSummary.test.ts` — new fixture + assertions

## TDD Evidence
- RED: `test_side_aware_leaders_friendly_dmg_taken` and `test_side_aware_leaders_null_when_no_friendly` failed before backend change
- RED: frontend test failed on `Leaders` type shape mismatch before `api.ts` + `hoverSummary.ts` change
- GREEN: all after implementation

## Test Suite Results
- Backend: `uv run pytest -q` — <N> passed
- Frontend: `npm test` — <N> passed
- TypeScript: `npx tsc --noEmit` — no errors
- Ruff: clean
- Mypy: clean

## Real-Data Sample (fight 8 BR)
```json
{leaders sample pasted here}
```
```

Fill in the actual numbers and JSON from Step 4.

---

## Self-Review Checklist

- [x] Spec: "3 fields per bucket" — covered in Tasks 1–3 (backend) and 4–5 (frontend)
- [x] Spec: side mechanism reused — `_build_char_side_map` uses same `alliance_id` lookup as `classify_entity`; no new side logic invented
- [x] Spec: TDD both layers — RED tests written before implementation in Tasks 1 and 4
- [x] Spec: drop old 4 fields — `Leaders`/`LeadersOut`/TypeScript `Leaders` all reduced to 3 fields
- [x] Spec: drop side-total lines from hover — `renderHoverSummary` no longer calls `seriesVal`; `FleetView` import kept in signature but `_view` is unused
- [x] Spec: HTML-escape names — `esc()` still applied in `leaderLine()`
- [x] Spec: `fmtCompact` for amounts — used in `leaderLine()`
- [x] Spec: "no log data" fallback — preserved in `allNull()` check
- [x] Spec: alignment `len(leaders)==len(x)` — unchanged; test `test_leaders_aligned_to_x_and_null_when_absent` preserved
- [x] Spec: `[]` when no logs — unchanged; test `test_leaders_empty_for_no_logs` preserved
- [x] Spec: real-data check — Task 6 Step 4
- [x] Spec: report at `.superpowers/sdd/hover-targets-report.md` — Task 6 Step 5
- [x] Spec: commit messages with Co-Authored-By — in Tasks 3 and 5
- [x] No placeholder steps — all steps have complete code
- [x] Type consistency — `top_friendly_dmg_taken` used in Python dataclass, Pydantic schema, TypeScript interface, test assertions, and hover renderer
