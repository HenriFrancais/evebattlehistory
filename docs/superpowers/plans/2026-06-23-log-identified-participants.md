# Log-identified off-BR participants + FC/HC logs-list sort — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add off-BR fight participants identified from logs (resolved to a known character) to the fleet composition on the correct side, with a best-effort/FC-assignable ship; and sort the FC/HC coverage logs list by user then character.

**Architecture:** A read-time module (`offbr_participants.py`) identifies off-BR characters from already-persisted `LogEvent`/`Character` data; `fleet_composition` folds them in (side via the existing `classify_entity`, ship via override→detected→Unknown) and `br_entities` surfaces their alliances/corps in the sides editor. ESI name→id→affiliation resolution happens at **write time** (log upload), persisting `Character`/`Alliance`/`Corporation`, so the GET read-paths never touch ESI. A new per-character `BrCharShip` override + endpoints back an inline ship picker.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async (SQLite/aiosqlite), pytest-asyncio; React + TypeScript (Vite, Vitest).

## Global Constraints

- Read (GET) endpoints MUST NOT hit ESI or commit (event-loop + SQLite write-lock). ESI + persistence happen only on write paths.
- Date/time: ISO `YYYY-MM-DD`, 24h UTC.
- Side classification is `classify_entity` (baseline blues + `BrSideOverride`); side allocation stays entity-level. Ship allocation is per-character (`BrCharShip`).
- FC/HC gate = `can_create_br(current_user(request))`.
- Tests use `data_source="demo"`; the demo ESI client must provide any new methods.
- Run backend tests with `.venv/bin/python -m pytest`. The aiosqlite "Event loop is closed" teardown warning is pre-existing noise.

---

### Task 1: Sort the FC/HC coverage logs list (Part 2)

**Files:**
- Modify: `app/logs/coverage.py` (final `return` of `br_coverage`, ~line 182)
- Test: `tests/test_coverage.py` (create if absent) or extend existing coverage test

**Interfaces:**
- Produces: `br_coverage(...)` returns `list[UserCoverage]` ordered by `user_name` (case-insensitive), each `.characters` ordered by `character_name` (case-insensitive).

- [ ] **Step 1: Failing test** — seed a BR with two users out of alphabetical order, each with two characters out of order; assert returned order.

```python
# tests/test_coverage.py
@pytest.mark.asyncio
async def test_br_coverage_sorted_by_user_then_character(db_session_maker, monkeypatch):
    # Build a roster with users "Zeb" (chars "Yara","Abe") and "Amy" (chars "Tom","Bo"),
    # all participating via a stamped LogEvent in one BR fight. (Use the file's existing
    # roster/seed helpers.) Then:
    result = await br_coverage(session, settings, br_id)
    assert [u.user_name for u in result] == ["Amy", "Zeb"]
    amy = next(u for u in result if u.user_name == "Amy")
    assert [c.character_name for c in amy.characters] == ["Bo", "Tom"]
```

- [ ] **Step 2: Run, verify FAIL** — `Run: .venv/bin/python -m pytest tests/test_coverage.py -k sorted -v` → FAIL (order mismatch).

- [ ] **Step 3: Implement** — replace the final return in `br_coverage`:

```python
    return [
        UserCoverage(
            user_name=un,
            characters=sorted(chars, key=lambda c: (c.character_name or "").lower()),
        )
        for un, chars in sorted(user_map.items(), key=lambda kv: kv[0].lower())
    ]
```

- [ ] **Step 4: Run, verify PASS.** Also run the existing coverage tests to ensure no regression.

- [ ] **Step 5: Commit** — `feat(coverage): sort FC/HC logs list by user then character`

---

### Task 2: ESI client — `resolve_ids` + `resolve_affiliations` (+ demo stubs)

**Files:**
- Modify: `app/esi/client.py` (add two methods to `EsiClient`)
- Modify: `app/esi/demo.py` (add matching methods to `DemoEsiClient`, fixture-backed)
- Test: `tests/test_esi_resolve.py` (create)

**Interfaces:**
- Produces:
  - `EsiClient.resolve_ids(names: list[str]) -> dict[str, int]` — name (exact) → character_id, via `POST /universe/ids/`, characters only.
  - `EsiClient.resolve_affiliations(char_ids: list[int]) -> dict[int, tuple[int|None,int|None]]` — char_id → (corporation_id, alliance_id), via `POST /characters/affiliation/`.
  - `DemoEsiClient` implements both, reading `data_demo/ids.json` / `data_demo/affiliations.json` (empty dict if absent).

- [ ] **Step 1: Failing test** (demo client, fixture-backed):

```python
# tests/test_esi_resolve.py
@pytest.mark.asyncio
async def test_demo_resolve_ids_and_affiliations(tmp_path):
    (tmp_path / "ids.json").write_text('{"Bob Pilot": 100, "Cap Sula": 101}')
    (tmp_path / "affiliations.json").write_text('{"100": {"corporation_id": 5, "alliance_id": 9}}')
    from app.esi.demo import DemoEsiClient
    c = DemoEsiClient(tmp_path)
    assert await c.resolve_ids(["Bob Pilot", "Nope"]) == {"Bob Pilot": 100}
    assert await c.resolve_affiliations([100, 101]) == {100: (5, 9), 101: (None, None)}
```

- [ ] **Step 2: Run, verify FAIL** (methods missing).

- [ ] **Step 3: Implement demo methods** in `DemoEsiClient`:

```python
    async def resolve_ids(self, names: list[str]) -> dict[str, int]:
        p = self._dir / "ids.json"
        table: dict[str, int] = json.loads(p.read_text()) if p.exists() else {}
        return {n: int(table[n]) for n in names if n in table}

    async def resolve_affiliations(self, char_ids):  # -> dict[int, tuple[int|None,int|None]]
        p = self._dir / "affiliations.json"
        table = json.loads(p.read_text()) if p.exists() else {}
        out = {}
        for cid in char_ids:
            row = table.get(str(cid)) or {}
            out[int(cid)] = (row.get("corporation_id"), row.get("alliance_id"))
        return out
```

- [ ] **Step 4: Implement real methods** in `EsiClient` (mirror `_post_names_chunk` style; `/universe/ids/` returns `{"characters":[{"id","name"}],...}`; `/characters/affiliation/` returns `[{"character_id","corporation_id","alliance_id"?}]`). Chunk ≤1000; best-effort (log+empty on error).

- [ ] **Step 5: Run, verify PASS. Commit** — `feat(esi): resolve_ids + resolve_affiliations (+ demo stubs)`

---

### Task 3: `BrCharShip` model (per-character ship override)

**Files:**
- Modify: `app/db/models.py` (new model + the ALTER comment block near `LogEvent`)
- Test: `tests/test_brcharship_model.py` (create)

**Interfaces:**
- Produces: `BrCharShip(br_id: str, character_id: int [PK pair], ship_type_id: int, set_by_user: str|None, set_at: datetime)`.

- [ ] **Step 1: Failing test** — insert a `BrCharShip`, read it back by `(br_id, character_id)`.
- [ ] **Step 2: Run, verify FAIL** (model missing).
- [ ] **Step 3: Implement model:**

```python
class BrCharShip(Base):
    """FC/HC per-character ship assignment for off-BR (log-identified) participants."""
    __tablename__ = "br_char_ship"
    br_id: Mapped[str] = mapped_column(String(64), ForeignKey("battle_report.br_id", ondelete="CASCADE", **_FK), primary_key=True)
    character_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ship_type_id: Mapped[int] = mapped_column(BigInteger)
    set_by_user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    set_at: Mapped[dt.datetime] = mapped_column(DateTime)
```

Add to the schema-migration comment near `LogEvent`: `CREATE TABLE br_char_ship (...)`.

- [ ] **Step 4: Run, verify PASS. Commit** — `feat(model): BrCharShip per-character ship override`

---

### Task 4: Write-time counterparty resolution (`resolve_log_characters`)

**Files:**
- Create: `app/fights/offbr_resolve.py`
- Modify: `app/api/logs.py` (call after ingest, on the write path)
- Test: `tests/test_offbr_resolve.py` (create)

**Interfaces:**
- Consumes: `EsiClient.resolve_ids`, `resolve_affiliations`, `resolve_names` (Task 2 + existing).
- Produces: `async def resolve_log_characters(session, settings, names: set[str]) -> int` — for names not matching an existing `Character.name` (case-insensitive), ESI-resolve to character ids (category character), fetch affiliations, and **persist** `Character` (+ `Alliance`/`Corporation` with resolved names). Returns count newly persisted. Best-effort: ESI failure logs and returns 0 without raising. Commits within the write path (caller commits).

- [ ] **Step 1: Failing test** — with a `DemoEsiClient` fixture mapping `"Hostile One"→700`, affiliation `700→(corp 70, alliance 77)`, and names file for 70/77: call `resolve_log_characters(session, settings, {"Hostile One","Already Known"})`; assert a `Character(700)` persisted with corp 70/alliance 77, `Corporation(70)`/`Alliance(77)` present with names; existing char untouched; junk name ignored.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** `resolve_log_characters`: lowercase-match against `Character.name`; ESI `resolve_ids` for the rest; `resolve_affiliations`; `resolve_names` for corp/alliance display names; upsert rows (`session.merge`). Pre-filter empty/all-non-alphanumeric tokens.
- [ ] **Step 4: Hook into upload** — in `app/api/logs.py`, after files are ingested+associated, gather the uploaded files' distinct counterparty names (`other_name`/`source_name`/`target_name` from the new events) and `await resolve_log_characters(...)` before commit. Guard with `settings.data_source`/try-except so upload never fails on ESI.
- [ ] **Step 5: Run, verify PASS. Commit** — `feat(logs): resolve off-BR counterparty characters via ESI on upload`

---

### Task 5: `offbr_participants.py` — read-time identification

**Files:**
- Create: `app/fights/offbr_participants.py`
- Test: `tests/test_offbr_participants.py` (create)

**Interfaces:**
- Consumes: existing `fight_participant_char_ids`, `br_logged_char_ids`; persisted `Character`, `LogEvent`, `InventoryType`.
- Produces:
  ```python
  @dataclass
  class OffBrChar:
      character_id: int
      character_name: str | None
      alliance_id: int | None
      corporation_id: int | None
      detected_ship_type_id: int | None
      reps_out: float
      user_name: str | None
      source: str  # "log_owner" | "counterparty"
  async def offbr_log_characters(session, settings, br_id) -> list[OffBrChar]
  ```
  Pure read: identify off-BR characters (log-uploaders + counterparties exact-matched to `Character`), load corp/alliance from `Character`, detected ship = most-common `other_ship_name`→`InventoryType` where the char is the counterparty, reps from the existing rep helper. No ESI, no commit.

- [ ] **Step 1: Failing test** — seed fight with on-BR attacker, an off-BR log-uploader (`Character` w/ corp/alliance) with stamped LogEvents, and an off-BR counterparty name matching a `Character`; assert both returned, on-BR excluded, detected ship resolved from `other_ship_name`.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** per the interface. Reuse `_reps_applied_by_char` (import from composition) or compute reps inline; dedupe by `character_id`.
- [ ] **Step 4: Run, verify PASS. Commit** — `feat(fights): offbr_log_characters read-time identification`

---

### Task 6: `br_entities` — union off-BR participant entities

**Files:**
- Modify: `app/analytics/sides_config.py` (`br_entities`)
- Test: `tests/test_sides_config.py` (extend) / `tests/test_offbr_participants.py`

**Interfaces:**
- Consumes: `offbr_log_characters` (Task 5). Adds `settings` param to `br_entities` if not present (needed to call the helper).
- Produces: `br_entities` output additionally includes alliance/corp entities present only via off-BR participants (deduped), each classified + named as today.

- [ ] **Step 1: Failing test** — a hostile counterparty whose alliance is on no killmail appears in `br_entities` (side `unassigned`); after inserting a `BrSideOverride` for that alliance→hostile, it classifies hostile.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** — union the `(alliance_id, corporation_id)` of `offbr_log_characters(...)` into `pairs` before name resolution; update `get_sides`/callers to pass `settings`.
- [ ] **Step 4: Run, verify PASS. Commit** — `feat(sides): surface off-BR participant entities in the sides editor`

---

### Task 7: `fleet_composition` — fold in off-BR participants

**Files:**
- Modify: `app/analytics/composition.py` (add params + folding), `CompositionPilot` gains `from_logs`
- Modify: `app/api/fleet.py` (pass `BrCharShip` overrides; `CompositionPilotOut.from_logs`)
- Modify: `app/api/schemas.py` (CompositionPilotOut + `from_logs`)
- Test: `tests/test_composition.py` (extend)

**Interfaces:**
- Consumes: `offbr_log_characters` (Task 5), `BrCharShip` overrides map `{character_id: ship_type_id}`.
- Produces: `CompositionPilot.from_logs: bool`; off-BR participants appear on their classified side; ship = override→detected→None; a known ship counts in that ship's tally (added to `acc.hulls`); pilot_count includes them.

- [ ] **Step 1: Failing test** — `test_composition_includes_offbr_log_participant`: off-BR friendly log-uploader (baseline alliance) with a detected Guardian appears on friendly with `from_logs=True`, increments the Guardian count; an Unknown-ship one appears hull-less, `from_logs=True`, not in any ship tally; pilot_count includes both.
- [ ] **Step 2: Failing test** — `test_composition_offbr_ship_override_wins`: a `BrCharShip` override changes the participant's hull and which tally it joins.
- [ ] **Step 3: Run, verify FAIL.**
- [ ] **Step 4: Implement** — in `fleet_composition`, after building killmail `acc` and `known_char_side`, call `offbr_log_characters`, load `BrCharShip` for the br, and for each off-BR char not in `acc`: classify side via `_side(alli, corp)`; ship = override or detected; add a `_Acc` with that hull (or none) and a new `from_logs` marker carried onto `CompositionPilot`. Ensure the no-hull render path sets `from_logs`. Add `from_logs` through `CompositionPilotOut`.
- [ ] **Step 5: Run, verify PASS. Commit** — `feat(composition): include off-BR log-identified participants`

---

### Task 8: Ship endpoints — PUT override + GET ship-type search

**Files:**
- Modify: `app/api/fleet.py` (or `app/api/sides.py`) — two routes
- Modify: `app/api/schemas.py` — `ShipOverrideIn`, `ShipTypeOut`
- Test: `tests/test_ship_override_api.py` (create)

**Interfaces:**
- Produces:
  - `PUT /api/brs/{br_id}/participants/{character_id}/ship` body `{ship_type_id: int|null}` — FC/HC only; upserts/clears `BrCharShip`; returns 204/200.
  - `GET /api/ship-types?q=<substr>` — returns `[{type_id, name}]` from `InventoryType` ship categories (`SHIP_LIKE_CATEGORIES`), name `ILIKE %q%`, capped (e.g. 25). Public read.

- [ ] **Step 1: Failing tests** — PUT as FC sets a `BrCharShip`; PUT `null` clears it; PUT as non-FC → 403; GET search returns matching ships only.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** both routes (mirror `app/api/sides.py` auth + upsert-by-delete pattern; `set_at` via `datetime.now(UTC)`).
- [ ] **Step 4: Run, verify PASS. Commit** — `feat(api): per-character ship override + ship-type search`

---

### Task 9: Frontend — from-logs marker, Unknown row, inline ship picker

**Files:**
- Modify: `frontend/src/components/FleetsPanel.tsx`, `frontend/src/styles/app.css`, `frontend/src/api.ts`, types file (`frontend/src/types.ts` or inline)
- Create: `frontend/src/components/ShipPicker.tsx`
- Test: `frontend/src/components/FleetsPanel.test.tsx` (extend), `ShipPicker.test.tsx` (create)

**Interfaces:**
- Consumes: `CompositionPilot.from_logs`; `GET /api/ship-types`; `PUT …/ship`.
- Produces: from-logs rows show a dimmed `📋 from logs` marker; Unknown-ship from-logs pilots render an inline `ShipPicker` for FC/HC (`by_user_available`); By-ship adds a trailing `Unknown — N (from logs)` row per side.

- [ ] **Step 1: Failing tests** — (a) a pilot with `from_logs` renders the marker; (b) an FC sees a ship picker on an Unknown from-logs row and selecting calls `api.setParticipantShip`; (c) By-ship shows the Unknown-from-logs count row.
- [ ] **Step 2: Run, verify FAIL** — `Run: npm test` (in `frontend/`).
- [ ] **Step 3: Implement** — add `from_logs` to the `CompositionPilot` type; marker + dim in `PilotRow`; `ShipPicker` (debounced search via `api.searchShipTypes`, calls `api.setParticipantShip`); By-ship Unknown row; api client methods.
- [ ] **Step 4: Run, verify PASS** (`npm test`, `npx tsc -b`). **Commit** — `feat(fleets): from-logs marker + inline ship picker`

---

### Task 10: End-to-end validation + finalize

- [ ] **Step 1:** Full backend suite `.venv/bin/python -m pytest -q` green; frontend `npm test` + `npx tsc -b` green.
- [ ] **Step 2:** Validate against the fight-8 backup (scratchpad DB+logs) that `sexy'beast` appears on the friendly side as a from-logs participant.
- [ ] **Step 3:** Update `docs/.../specs` status to Implemented; merge `feat/log-identified-participants` → master (no-ff) when the user approves.

## Self-Review notes

- Spec coverage: identification (T5), side via classify_entity (T7), sides-editor allocation (T6), ESI resolution write-time (T2,T4), per-character ship + counts (T3,T7,T8), from-logs marker/Unknown row/picker (T9), coverage sort (T1). All covered.
- Architecture change vs spec: ESI resolution moved to **write time** (T4) to honor the no-ESI-on-read constraint; reads (T5/T6/T7) are pure. Spec risk section anticipated this.
- Open detail resolved during impl: exact ESI JSON shapes (T2), and whether `_reps_applied_by_char` is importable from composition (T5).
