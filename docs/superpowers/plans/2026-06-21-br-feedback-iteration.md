# BR Feedback Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address post-deployment BR feedback — show battle date/time, make the fleet graph zoom resettable / snapshot clearable / time-bounded by typed input, add a hover summary of who is taking the most damage and reps, fix the "applied tackle" false positives, and surface far more from killmails (effective tank, per-attacker damage, weapon roles, item losses).

**Architecture:** FastAPI + async SQLAlchemy backend (`app/`), React 18 + Vite + TypeScript frontend (`frontend/`), uPlot canvas graph. Combat logs are an optional augmentation over zKillboard/ESI killmails. Work is ordered tackle-fix → quick UI → hover pipeline → killmail augmentation so the correctness bug and cheap wins land first.

**Tech Stack:** Python 3.11+, SQLAlchemy (async), Pydantic, pytest (`asyncio_mode=auto`); React, uPlot, vitest, Testing Library.

## Global Constraints

- **Repo root:** `/home/matron/dev/nv-wh-fight-history`. The shell cwd may reset elsewhere — always use absolute paths or `git -C /home/matron/dev/nv-wh-fight-history`. All work is on branch `feature/br-feedback-iteration`.
- **Backend test command:** `uv run pytest <path> -q` (config: `asyncio_mode = "auto"`, `testpaths = ["tests"]`; `async def test_*` run without per-test markers except the `tmp_path/monkeypatch` API tests which already carry `@pytest.mark.asyncio`).
- **Frontend test command:** from `frontend/`, `npm test` runs `vitest run --config vitest.config.ts`; a single file is `npm test -- <pathRelativeToFrontend>` (equivalently `npx vitest run --config vitest.config.ts <path>`).
- **Lint/types before each commit touching `app/`:** `uv run ruff check app tests` and `uv run mypy app`.
- **No Alembic.** Schema is built by `Base.metadata.create_all` (`app/db/engine.py`), which creates *missing tables* but never alters existing ones. New columns "just work" for tests (fresh DB via `init_models`) but a **pre-existing persistent SQLite DB needs a one-time manual `ALTER TABLE …` then a reparse** (`python -m app.logs.reparse`) to backfill. This applies only to Tasks 2 (LogEvent: 4 new columns) and 14 (Killmail.damage_taken).
- **Commit messages** end with the trailer (one blank line before it):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Reuse existing helpers, don't reinvent:** name resolution via `_resolve_char_names`; killmail-hull resolution mirrors `composition.py`; weapon string families via `classify_weapon` (`app/analytics/weapons.py`); log-vs-killmail seam via `fight_damage_reconcile` (`app/analytics/reconcile.py`). Reuse existing test fixtures (`_make_br_with_fight`, `_insert_fight`, `_insert_bucket`, `_make_roster_lookup`, `db_session_maker`).
- **Number formatting:** the frontend hover/leaderboard reuse the existing compact formatter in `frontend/src/format.ts`. Before writing those tasks, confirm the exact exported name (`fmtCompact`/`fmtNum`/`fmtIsk`) and use whichever exists — do not introduce a new formatter.

---

# Phase 1 — Tackle re-attribution + dedupe (§4)

Root cause: `app/logs/parse.py` `_match_ewar()` Case 3 (third-party observation, neither party is "you") records the line against the **log owner** with no authoritative flag; `app/analytics/ewar.py` then groups by owner `character_id` with no dedupe, so bystanders appear to tackle each other. Fix: parse both real parties + an `authoritative` flag, dedupe one physical tackle across logs, and aggregate applied/received against the real source/target.

**Storage decision:** add columns to `LogEvent` (fits the existing flat-row pattern used by ingest/reparse/associate; the "other party" is already a **name string** — there is no name→character_id resolution for the non-owner party, so the dedupe key uses names). Dedupe runs at the `associate.py` convergence point and marks duplicates with `dedupe_suppressed` rather than deleting (idempotent, reversible).

### Task 1: Parser extracts source/target/authoritative for all 3 EWAR cases

**Files:**
- Modify `app/logs/parse.py` — `_match_ewar` (~lines 141-179), `ParsedLogEvent` dataclass (~lines 387-401), `_EMPTY_EFFECT` (~lines 408-419), `parse_line` construction (~lines 465-478).
- Test `tests/test_log_parse.py` — extend `test_scram_fields` (lines 207-231); add 3 case tests.

**Interfaces:**
- Produces new effect dict keys + dataclass fields: `source_name: str | None`, `target_name: str | None`, `authoritative: bool`.
  - Case 1 (`src=="you"`): `authoritative=True`, `source_name=None`, `target_name=<tgt name>`, `direction="out"`, `other_name=<tgt name>`.
  - Case 2 (`tgt=="you"`): `authoritative=True`, `source_name=<src name>`, `target_name=None`, `direction="in"`, `other_name=<src name>`.
  - Case 3 (third-party): `authoritative=False`, `source_name=<src name>`, `target_name=<tgt name>`, `direction="in"`, **`other_name=<src name>` = real tackler (never the owner)**.

- [ ] **Step 1: Write failing tests.** Append to `tests/test_log_parse.py` after `test_scram_fields` (line 231):

```python
def test_scram_third_party_records_real_tackler_and_target() -> None:
    """Case 3: neither party is 'you' — record real source AND target, authoritative=False."""
    raw = (
        "[ 2026.01.01 12:06:44 ] (combat) "
        "<color=0xffffffff><b>Warp scramble attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>AllyChar Kyte</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[NV]</color></font>"
        "<font size=12>[NVACA]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Muninn</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Omen Navy Issue</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.effect_type == "scram"
    assert evt.authoritative is False
    assert evt.source_name == "AllyChar Kyte"
    assert evt.target_name == "FakeEnemy Delta"
    assert evt.other_name == "AllyChar Kyte"   # never the log owner


def test_disrupt_out_from_you_sets_authoritative_and_target() -> None:
    """Case 1: src=='you' → authoritative=True, source_name=None, target_name set."""
    raw = (
        "[ 2026.01.01 12:00:05 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>you</b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font>"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.authoritative is True
    assert evt.source_name is None
    assert evt.target_name == "FakeEnemy Delta"


def test_disrupt_in_to_you_sets_authoritative_and_source() -> None:
    """Case 2: tgt=='you' → authoritative=True, source_name set, target_name=None."""
    raw = (
        "[ 2026.01.01 12:00:06 ] (combat) "
        "<color=0xffffffff><b>Warp disruption attempt</b> "
        "<color=0x77ffffff><font size=10>from</font> "
        "<color=0xffffffff><b>"
        "<font size=12><color=0xFFFFFFFF><b>FakeEnemy Delta</b> </color></font>"
        "<font size=12><color=0xFFFFB300>[10MN]</color></font>"
        "<font size=12>[.EFG]</font> "
        "<font size=12><color=0xFFFFFFFF><b>Retribution</b></color></font></b> "
        "<color=0x77ffffff><font size=10>to <b><color=0xffffffff></font>you!"
    )
    evt = parse_line(raw)
    assert evt is not None
    assert evt.authoritative is True
    assert evt.source_name == "FakeEnemy Delta"
    assert evt.target_name is None
```
Also update the existing `test_scram_fields` (lines 226-230) so its trailing asserts match the NEW Case-3 contract: `other_name == "AllyChar Kyte"`, `direction == "in"`, `source_name == "AllyChar Kyte"`, `target_name == "FakeEnemy Delta"`, `authoritative is False`.

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_log_parse.py -q` → `AttributeError: ParsedLogEvent has no attribute authoritative`.

- [ ] **Step 3: Implement.** Rewrite `_match_ewar` (`app/logs/parse.py` lines 150-179):

```python
def _match_ewar(rest_stripped: str, rest_raw: str) -> dict[str, Any] | None:
    """Match warp disruption/scramble lines.

    Extracts BOTH parties for every line, plus an ``authoritative`` flag that is
    True iff one party is the log owner ("you"). Case 3 (third-party observation,
    neither party is "you") keeps the real source->target instead of folding the
    initiator into the log owner.
    """
    m = _EWAR_RE.match(rest_stripped)
    if not m:
        return None
    ewar_type = "disrupt" if m.group(1) == "disruption" else "scram"
    src_raw = m.group(2).strip()
    tgt_raw = m.group(3).strip()
    src_is_you = src_raw == "you"
    tgt_is_you = tgt_raw.rstrip("!") == "you"

    source_name: str | None = None if src_is_you else _parse_new_encoding(src_raw)[0]
    target_name: str | None = None if tgt_is_you else _parse_new_encoding(tgt_raw)[0]
    authoritative = src_is_you or tgt_is_you

    if src_is_you:
        direction: Literal["in", "out"] = "out"
        name, corp, alli, ship = _parse_new_encoding(tgt_raw)
    elif tgt_is_you:
        direction = "in"
        name, corp, alli, ship = _parse_new_encoding(src_raw)
    else:
        # Third-party: record the REAL initiator (source), never the log owner.
        direction = "in"
        name, corp, alli, ship = _parse_new_encoding(src_raw)

    return {
        "effect_type": ewar_type,
        "direction": direction,
        "amount": None,
        "other_name": name,
        "other_corp_ticker": corp,
        "other_alliance_ticker": alli,
        "other_ship_name": ship,
        "module_name": None,
        "quality": None,
        "source_name": source_name,
        "target_name": target_name,
        "authoritative": authoritative,
    }
```
Add to `ParsedLogEvent` (after `module_name: str | None`, ~line 400):
```python
    source_name: str | None = None
    target_name: str | None = None
    authoritative: bool = False
```
Add to `_EMPTY_EFFECT` (~line 408): `"source_name": None, "target_name": None, "authoritative": False,`.
Add to the `parse_line` `ParsedLogEvent(...)` construction (after `module_name=…`, ~line 477):
```python
        source_name=effect.get("source_name"),
        target_name=effect.get("target_name"),
        authoritative=bool(effect.get("authoritative")),
```

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_log_parse.py -q`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/logs/parse.py tests/test_log_parse.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(logs): parse source/target/authoritative for all EWAR cases' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 2: Add EWAR-attribution columns to LogEvent (incl. dedupe_suppressed)

**Files:**
- Modify `app/db/models.py` — `LogEvent` (~lines 410-437): 4 new columns + a dedupe index.
- Test `tests/test_log_ingest.py` — add a column-presence assertion near the existing table-existence test (~line 50).

**Interfaces:**
- New `LogEvent` columns: `source_name: str | None` (String(128)), `target_name: str | None` (String(128)), `authoritative: bool` (Boolean, default False), `dedupe_suppressed: bool` (Boolean, default False).
- New index `ix_log_event_ewar_dedupe` on `(fight_id, effect_type, source_name, target_name)`.

> `dedupe_suppressed` is added here (not in Task 4) so the schema changes once; Task 4 only writes to it.

- [ ] **Step 1: Write failing test.** Add to `tests/test_log_ingest.py` near line 50:
```python
async def test_log_event_has_ewar_attribution_columns(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """LogEvent must expose source_name, target_name, authoritative, dedupe_suppressed."""
    from sqlalchemy import inspect as sa_inspect
    from app.db.engine import get_engine
    from app.config import get_settings

    engine = get_engine(get_settings())
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sc: {c["name"] for c in sa_inspect(sc).get_columns("log_event")}
        )
    assert {"source_name", "target_name", "authoritative", "dedupe_suppressed"} <= cols
```
(Match the settings/engine setup already used in that file if it differs.)

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_log_ingest.py -q -k ewar_attribution` → columns absent.

- [ ] **Step 3: Implement.** In `app/db/models.py` `LogEvent`, after `module_name` (~line 432), before `fight_id`:
```python
    source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authoritative: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
```
Add to `__table_args__` (after `Index("ix_log_event_fight_id", "fight_id")`):
```python
        Index("ix_log_event_ewar_dedupe", "fight_id", "effect_type", "source_name", "target_name"),
```
Add a comment above `LogEvent`: schema is created via `Base.metadata.create_all` (no Alembic); an existing populated DB needs `ALTER TABLE log_event ADD COLUMN source_name VARCHAR(128); … target_name VARCHAR(128); … authoritative BOOLEAN DEFAULT 0; … dedupe_suppressed BOOLEAN DEFAULT 0;` then `python -m app.logs.reparse`.

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_log_ingest.py -q -k ewar_attribution`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/db/models.py tests/test_log_ingest.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(db): add source/target/authoritative/dedupe_suppressed to LogEvent' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 3: Ingest + reparse persist the new EWAR attribution fields

**Files:**
- Modify `app/logs/ingest.py` — `LogEvent(...)` construction (~lines 172-189).
- Modify `app/logs/reparse.py` — `LogEvent(...)` construction (~lines 65-72).
- Test `tests/test_log_ingest.py` — ingest a Case-3 scram, assert real-tackler attribution.

**Interfaces:** consumes `ParsedLogEvent.source_name/.target_name/.authoritative`; produces persisted `LogEvent.source_name/.target_name/.authoritative`. The SDE `split_entity` post-processing must NOT touch `source_name`/`target_name` (already clean names) — carry them verbatim.

- [ ] **Step 1: Write failing test.** Add to `tests/test_log_ingest.py` (reuse the fixture + `_make_roster_lookup`). Define `_THIRD_PARTY_SCRAM_LOG_BYTES` as a gamelog header + the Case-3 scram line, owner `TestChar Alpha` (char 2112615087):
```python
async def test_ingest_third_party_scram_attributes_real_tackler(db_session_maker, tmp_settings) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select
    from app.db.models import LogEvent
    from app.logs.ingest import ingest_log

    raw = _THIRD_PARTY_SCRAM_LOG_BYTES
    async with db_session_maker() as session:
        await ingest_log(
            session, tmp_settings, "Ra'zok", "20260101_120000_2112615087.txt", raw,
            _make_roster_lookup({"testchar alpha": 2112615087}),
        )
        await session.commit()
        row = (await session.execute(
            select(LogEvent).where(LogEvent.effect_type == "scram")
        )).scalar_one()
    assert row.authoritative is False
    assert row.source_name == "AllyChar Kyte"
    assert row.target_name == "FakeEnemy Delta"
    assert row.other_name == "AllyChar Kyte"   # real tackler
    assert row.character_id == 2112615087       # owner column still the file owner
```

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_log_ingest.py -q -k third_party_scram` → `source_name is None`.

- [ ] **Step 3: Implement.** In `app/logs/ingest.py` step-8 list comprehension (after `module_name=e.module_name,`, ~line 187):
```python
                source_name=e.source_name,
                target_name=e.target_name,
                authoritative=e.authoritative,
```
In `app/logs/reparse.py` `LogEvent(...)` (after `module_name=e.module_name,`, ~line 69):
```python
                    source_name=e.source_name, target_name=e.target_name,
                    authoritative=e.authoritative,
```
Verify the `split_entity` blocks (`ingest.py` ~164-167, `reparse.py` ~56-64) only mutate `other_name`/`other_ship`.

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_log_ingest.py -q -k third_party_scram`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/logs/ingest.py app/logs/reparse.py tests/test_log_ingest.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(logs): persist source/target/authoritative on ingest + reparse' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 4: Dedupe tackle relationships across logs (associate.py)

**Files:**
- Modify `app/logs/associate.py` — add `_dedupe_ewar_relationships(session, fight_ids)` (after `_rebuild_buckets_for_pairs`, ~line 140) and call it from `associate_file` (after step 5, ~line 324) and `associate_logs_for_br` (after the per-file loop, ~line 395).
- Test `tests/test_association.py` — multi-log fixture.

**Interfaces:**
- Dedupe key: `(fight_id, _floor_to_bucket(ts), source_name, target_name, effect_type)` for `effect_type ∈ {"scram","disrupt"}`.
- Keep one canonical row per group preferring `authoritative=True` then lowest `event_id`; set `dedupe_suppressed=True` on the rest (reversible/idempotent — reset suppression for the fights first so the pass is recomputable).
- Signature: `async def _dedupe_ewar_relationships(session: AsyncSession, fight_ids: set[int]) -> None`.

- [ ] **Step 1: Write failing test.** Add to `tests/test_association.py`:
```python
async def test_ewar_dedupe_one_relationship_across_two_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """Owner (authoritative) + bystander (non-authoritative) logs of the SAME scram in the
    SAME bucket collapse to ONE surviving row; bystander attribution dropped."""
    from sqlalchemy import select
    from app.db.models import LogEvent
    from app.logs.associate import _dedupe_ewar_relationships

    async with db_session_maker() as session:
        fight_id = await _insert_fight(
            session, victim_char_id=CHAR_A, attacker_char_id=CHAR_B,
            started_at=FIGHT_START, ended_at=FIGHT_END,
        )
        ts = FIGHT_START + dt.timedelta(seconds=3)
        f1 = await _insert_gamelog_file(session, character_id=CHAR_A)
        session.add(LogEvent(
            file_id=f1, character_id=CHAR_A, ts=ts, direction="out", effect_type="scram",
            source_name="OwnerChar", target_name="FakeEnemy Delta", authoritative=True,
            fight_id=fight_id,
        ))
        f2 = await _insert_gamelog_file(session, character_id=CHAR_B)
        session.add(LogEvent(
            file_id=f2, character_id=CHAR_B, ts=ts + dt.timedelta(seconds=1),
            direction="in", effect_type="scram",
            source_name="OwnerChar", target_name="FakeEnemy Delta", authoritative=False,
            fight_id=fight_id,
        ))
        await session.flush()

        await _dedupe_ewar_relationships(session, {fight_id})

        surviving = (await session.execute(
            select(LogEvent).where(
                LogEvent.fight_id == fight_id,
                LogEvent.effect_type == "scram",
                LogEvent.dedupe_suppressed.is_(False),
            )
        )).scalars().all()
    assert len(surviving) == 1
    assert surviving[0].authoritative is True
```
(If `FIGHT_START` is not bucket-aligned, pin both events to `_floor_to_bucket(FIGHT_START)+1s/+2s` so they share a bucket.)

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_association.py -q -k ewar_dedupe` → `ImportError: _dedupe_ewar_relationships`.

- [ ] **Step 3: Implement.** Add to `app/logs/associate.py` (after `_rebuild_buckets_for_pairs`, ~line 140); ensure `update` and `defaultdict` are imported:
```python
_EWAR_DEDUPE_TYPES = ("scram", "disrupt")


async def _dedupe_ewar_relationships(session: AsyncSession, fight_ids: set[int]) -> None:
    """Collapse duplicate tackle observations seen across multiple logs.

    One physical tackle can be logged by the tackler (authoritative), the target,
    and any number of third-party observers. Group EWAR events by
    (fight_id, floor(ts)->bucket, source_name, target_name, effect_type); keep one
    canonical row per group preferring authoritative=True, and set
    ``dedupe_suppressed=True`` on the rest so the aggregator counts each tackle once.
    Reversible (no deletes) → idempotent under re-association/reparse.
    """
    if not fight_ids:
        return

    await session.execute(
        update(LogEvent)
        .where(LogEvent.fight_id.in_(list(fight_ids)),
               LogEvent.effect_type.in_(list(_EWAR_DEDUPE_TYPES)))
        .values(dedupe_suppressed=False)
    )

    rows = (await session.execute(
        select(
            LogEvent.event_id, LogEvent.fight_id, LogEvent.ts,
            LogEvent.source_name, LogEvent.target_name,
            LogEvent.effect_type, LogEvent.authoritative,
        ).where(
            LogEvent.fight_id.in_(list(fight_ids)),
            LogEvent.effect_type.in_(list(_EWAR_DEDUPE_TYPES)),
        )
    )).all()

    groups: dict[tuple[int, dt.datetime, str | None, str | None, str], list[tuple[int, bool]]] = (
        defaultdict(list)
    )
    for event_id, fid, ts, src, tgt, etype, auth in rows:
        groups[(fid, _floor_to_bucket(ts), src, tgt, etype)].append((event_id, bool(auth)))

    suppress_ids: list[int] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (not m[1], m[0]))  # authoritative first, then event_id
        suppress_ids.extend(eid for eid, _ in members[1:])

    if suppress_ids:
        await session.execute(
            update(LogEvent).where(LogEvent.event_id.in_(suppress_ids))
            .values(dedupe_suppressed=True)
        )
    await session.flush()
```
Call it from `associate_file` after `_rebuild_buckets_for_pairs(...)` with the fight ids touched (`{fid for fid, _ in all_pairs}`), and from `associate_logs_for_br` after the per-file loop with `{f.fight_id for f in fights}`. The reparse flow calls `associate_file` (via `associate_file_to_all`), so the per-file call covers reparse.

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_association.py -q -k ewar_dedupe`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/logs/associate.py tests/test_association.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(logs): dedupe tackle relationships by (fight,bucket,source,target,effect)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 5: ewar.py aggregates applied/received by source/target from the deduped set

**Files:**
- Modify `app/analytics/ewar.py` — `EwarRow` dataclass (~lines 57-66) and `fight_ewar` EWAR branch (~lines 117-200). Only the EWAR (`scram`/`disrupt`/`jam`) path changes; `cap`/`logi` stay grouped by `character_id`.
- Modify `app/api/schemas.py` (`EwarRowOut`, lines 229-237) and `app/api/analytics.py` (`EwarRowOut(...)` mapping, ~lines 115-123).
- Test `tests/test_ewar.py`.

**Interfaces:**
- `EwarRow` gains `source_name: str | None = None`, `target_name: str | None = None`.
- For `effect_type ∈ {scram,disrupt}`: select from `LogEvent` filtered by `fight_id`, `effect_type`, **`dedupe_suppressed.is_(False)`**, grouped by `(effect_type, source_name, target_name, direction)`; count **applied** against `source_name`, **received** against `target_name`. Skip degenerate rows where `source_name == target_name`. `jam` keeps its current single-party path; `cap`/`logi` unchanged.
- `EwarRowOut` gains `source_name: str | None`, `target_name: str | None`.

- [ ] **Step 1: Write failing test.** Add to `tests/test_ewar.py` (extend the local `_add_event` helper to accept `source_name`/`target_name`/`authoritative`/`dedupe_suppressed`):
```python
async def test_ewar_no_friendly_on_friendly_attribution(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    """A suppressed third-party friendly-on-friendly observation must not appear; the
    authoritative row names the real source/target."""
    from app.analytics.ewar import fight_ewar
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _add_event(session, fight_id, CHAR_C, "in", "scram",
                         source_name="AllyChar Kyte", target_name="AllyChar Boop",
                         authoritative=False, dedupe_suppressed=True)
        await _add_event(session, fight_id, CHAR_A, "out", "scram",
                         source_name="AllyChar Kyte", target_name="FakeEnemy Delta",
                         authoritative=True, dedupe_suppressed=False)
        await session.commit()
        result = await fight_ewar(session, fight_id)
    sources = {r.source_name for r in result.ewar}
    targets = {r.target_name for r in result.ewar}
    assert "AllyChar Boop" not in targets       # suppressed friendly-on-friendly gone
    assert "AllyChar Kyte" in sources            # real tackler counted once
    assert "FakeEnemy Delta" in targets
```

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_ewar.py -q -k friendly_on_friendly` → `EwarRow` has no `source_name`.

- [ ] **Step 3: Implement.** In `app/analytics/ewar.py`: add `source_name`/`target_name` to `EwarRow`; replace the EWAR branch so scram/disrupt select from `LogEvent` with the `dedupe_suppressed.is_(False)` filter, grouped by `(effect_type, source_name, target_name, direction)`, counting applied per `source_name` and received per `target_name`, skipping `source_name == target_name`. Populate the new fields on each `EwarRow`. Update `app/api/schemas.py` `EwarRowOut` (+`source_name`/`target_name`) and `app/api/analytics.py` to pass them through. Adjust any existing `test_ewar.py` cases that assert the old `character_id`-keyed shape.

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_ewar.py -q`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/ewar.py app/api/schemas.py app/api/analytics.py tests/test_ewar.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(ewar): aggregate by source/target from deduped set; no friendly-on-friendly' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

**Phase 1 backfill (after Task 5):** on a pre-existing prod DB run the 4× `ALTER TABLE log_event ADD COLUMN …` then `python -m app.logs.reparse`. Fresh/test DBs need nothing.

---

# Phase 2 — Battle date/time + graph controls (§1, §2)

Frontend-only. `battle_at`/`created_at` already reach the client; `fmtDateTime`/`isoToEpoch` already exist; the snapshot `range` state is already lifted to `BrDetailPage` and threaded as `selectedRange`/`onSelectRange`. `FleetTimeline.t_start`/`t_end` (epoch seconds) bound the typed inputs. All commands run from `frontend/`.

### Task 6: "Battle (UTC)" stat on the BR detail header

**Files:**
- Modify `frontend/src/views/BrDetailPage.tsx` (format import line 11; summary flex row ~lines 571-577).
- Test `frontend/src/views/BrDetailPage.test.tsx`.

**Interfaces:** consumes `BrDetail.battle_at`/`created_at`, `fmtDateTime`; produces a stat block labelled "Battle (UTC)" = `fmtDateTime(br.battle_at ?? br.created_at)`.

- [ ] **Step 1: Write failing test.** Append inside `describe('BrDetailPage', …)`, reusing the file's existing `mockBr` (`battle_at: '2026-06-10T18:00:00Z'`) and its ready-BR render/mocks helper (mirror the existing "renders summary" test exactly; add `within` to the testing-library import if absent):
```tsx
  it('shows the Battle (UTC) datetime in the summary header', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue({
      user_name: 'Tester', user_rank: 'member', user_teams: [],
      main_character_id: '1', can_create_br: false, impersonation_available: false,
    } as MeResponse)
    vi.mocked(api.myBrCoverage).mockRejectedValue(new ApiError(404, 'none'))
    render(
      <MemoryRouter initialEntries={['/brs/br1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes><Route path="/brs/:id" element={<BrDetailPage />} /></Routes>
      </MemoryRouter>,
    )
    const summary = await screen.findByTestId('summary-section')
    expect(within(summary).getByText('Battle (UTC)')).toBeInTheDocument()
    expect(within(summary).getByText('2026-06-10 18:00')).toBeInTheDocument()
  })
```
(If `api.getBr`/`api.me`/`mockBr`/`MeResponse` names differ in the file, use whatever that file already uses.)

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/views/BrDetailPage.test.tsx` → "Unable to find … Battle (UTC)".

- [ ] **Step 3: Implement.** Line 11: `import { fmtIsk, fmtDateTime } from '../format'`. Insert as the first child of the summary flex row (before the System stat):
```tsx
          <div>
            <div className="stat-label">Battle (UTC)</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {fmtDateTime(br.battle_at ?? br.created_at)}
            </div>
          </div>
```

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/views/BrDetailPage.test.tsx`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/views/BrDetailPage.tsx frontend/src/views/BrDetailPage.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(br-detail): show Battle (UTC) datetime in summary header' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 7: BR list shows full date+time

**Files:**
- Modify `frontend/src/components/BrTimelineTable.tsx` (import line 9; sub-line lines 78-80).
- Test `frontend/src/components/BrTimelineTable.test.tsx`.

**Interfaces:** consumes `BrSummary.battle_at`/`created_at`, `fmtDateTime`; sub-line becomes `fmtDateTime(br.battle_at ?? br.created_at)`; month grouping unchanged.

- [ ] **Step 1: Write failing test.** Append inside `describe('BrTimelineTable', …)`, reusing `makeBr`/`renderTable` (`battle_at` default `'2026-06-10T18:30:00Z'`):
```tsx
  it('shows full UTC date and time in the battle sub-line, keeping month groups', () => {
    renderTable([makeBr()])
    const row = screen.getByTestId('timeline-row')
    expect(within(row).getByText('2026-06-10 18:30')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: '2026-06' })).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/components/BrTimelineTable.test.tsx` → current code renders `'18:30'`.

- [ ] **Step 3: Implement.** Line 9: `import { fmtIsk, fmtDateTime } from '../format'`. Change the sub-line (lines 78-80) `fmtTime(...)` → `fmtDateTime(br.battle_at ?? br.created_at)`. (`fmtTime` becomes unused — removing it from the import keeps lint clean.)

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/components/BrTimelineTable.test.tsx`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/components/BrTimelineTable.tsx frontend/src/components/BrTimelineTable.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(br-list): show full UTC date+time in battle column' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 8: "Reset zoom" button restoring full extent on all panels

**Files:**
- Modify `frontend/src/components/FleetGraph.tsx`: `PanelChartProps` (lines 304-317), `PanelChart` effect (lines 333-493), `FleetGraphCore` (lines 579-720).
- Test `frontend/src/components/FleetSection.test.tsx`.

**Interfaces:**
- New `PanelChart` prop `registerReset: (fn: () => void) => () => void`.
- `FleetGraphCore` renders a "Reset zoom" `<button>`; clicking calls every registered per-panel reset (`u.setScale('x', { min: fullMin, max: fullMax })`) and sets `zoomRef.current = null`.
- `fullMin`/`fullMax` are hoisted from the effect into a `useMemo` keyed `[kills, fights, x]`, consumed by both the build effect and the reset closure.

- [ ] **Step 1: Write failing test.** In `FleetSection.test.tsx`, extend the existing `vi.mock('uplot', …)` return object with a `setScale` spy that pushes to a module-level `setScaleCalls` array, reset it in `beforeEach`, then:
```tsx
  it('has a Reset zoom button that restores full x-extent on every panel', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    const user = userEvent.setup()
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    setScaleCalls.length = 0
    await user.click(screen.getByRole('button', { name: /reset zoom/i }))
    const xResets = setScaleCalls.filter((c) => c.key === 'x')
    expect(xResets.length).toBeGreaterThanOrEqual(3)
    for (const c of xResets) {
      expect(typeof c.range.min).toBe('number')
      expect(typeof c.range.max).toBe('number')
    }
  })
```
where near the top of the file:
```tsx
const setScaleCalls: { key: string; range: { min?: number; max?: number } }[] = []
```
and the mock-return gains:
```tsx
    setScale: vi.fn((key: string, range: { min?: number; max?: number }) => {
      setScaleCalls.push({ key, range })
    }),
```

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/components/FleetSection.test.tsx` → no "reset zoom" button.

- [ ] **Step 3: Implement.** (3a) Add `registerReset` to `PanelChartProps` and destructure it in `PanelChart`. (3b) Hoist the full-extent computation into a memo just before the build effect:
```tsx
  const { fullMin, fullMax } = useMemo(() => {
    const lossTimes: number[] = []
    for (const k of kills) lossTimes.push(k.ts)
    for (const f of fights) {
      if (f.started_at) lossTimes.push(isoToEpoch(f.started_at))
      if (f.ended_at) lossTimes.push(isoToEpoch(f.ended_at))
    }
    if (lossTimes.length) {
      const lo0 = Math.min(...lossTimes)
      const hi0 = Math.max(...lossTimes)
      const buf = Math.min(Math.max((hi0 - lo0) * 0.05, 15), 120)
      return { fullMin: lo0 - buf, fullMax: hi0 + buf }
    }
    return { fullMin: x.length ? x[0] : 0, fullMax: x.length ? x[x.length - 1] : 1 }
  }, [kills, fights, x])
```
Inside the build effect, delete the duplicate `let fullMin/fullMax` declarations and reuse `const lo = fullMin; const hi = fullMax;` for the clip/pin logic (keep `lossTimes` for the clip predicate); the existing `setScale` hook at lines 433-442 now reads the memo values unchanged. After the chart is created + saved zoom restored, register the reset and clean it up:
```tsx
    const unregisterReset = registerReset(() => {
      u.setScale?.('x', { min: fullMin, max: fullMax })
    })
```
```tsx
    return () => {
      window.removeEventListener('resize', onResize)
      clearDrag()
      unregisterReset()
      u.destroy()
    }
```
Add `registerReset, fullMin, fullMax` to the effect dependency array. (3c) In `FleetGraphCore`, after `positionersRef`:
```tsx
  const resettersRef = useRef<Set<() => void>>(new Set())
  const registerReset = useCallback((fn: () => void) => {
    resettersRef.current.add(fn)
    return () => { resettersRef.current.delete(fn) }
  }, [])
  const handleResetZoom = useCallback(() => {
    resettersRef.current.forEach((fn) => fn())
    zoomRef.current = null
  }, [])
```
Render the button at the end of `.fleet-controls`:
```tsx
        <button type="button" className="fleet-legend-btn" data-testid="reset-zoom-btn"
          onClick={handleResetZoom} style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}>
          Reset zoom
        </button>
```
and pass `registerReset={registerReset}` into `PanelChart`.

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/components/FleetSection.test.tsx`, then `npm test` to confirm no regressions.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/components/FleetGraph.tsx frontend/src/components/FleetSection.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(fleet-graph): add Reset zoom button restoring full extent on all panels' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 9: "Clear" button on the snapshot panel

**Files:**
- Modify `frontend/src/components/SnapshotPanel.tsx` (`Props` lines 151-156, signature line 158, header lines 196-198).
- Modify `frontend/src/views/BrDetailPage.tsx` (line 653).
- Test `frontend/src/components/SnapshotPanel.test.tsx`.

**Interfaces:** new optional prop `onClearRange?: () => void`; renders a "Clear" button when `range != null`; wired in `BrDetailPage` to `setRange(null)`.

- [ ] **Step 1: Write failing test.** Add to `SnapshotPanel.test.tsx` (add `userEvent` import if absent):
```tsx
  it('renders a Clear button when a range is set and calls onClearRange', async () => {
    vi.mocked(api.snapshot).mockResolvedValue({ from_ts: 1000, to_ts: 1010, rows: [] })
    const onClearRange = vi.fn()
    const user = userEvent.setup()
    render(<SnapshotPanel brId="br1" range={{ from: 1000, to: 1010 }} onClearRange={onClearRange} />)
    await user.click(await screen.findByTestId('snap-clear-btn'))
    expect(onClearRange).toHaveBeenCalledTimes(1)
  })

  it('shows no Clear button when range is null', () => {
    render(<SnapshotPanel brId="br1" range={null} onClearRange={vi.fn()} />)
    expect(screen.queryByTestId('snap-clear-btn')).not.toBeInTheDocument()
  })
```

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/components/SnapshotPanel.test.tsx` → no `snap-clear-btn`.

- [ ] **Step 3: Implement.** Add `onClearRange?: () => void` to `Props`, destructure it, and render in the header (`.contrib-head` already uses `justify-content: space-between`; `.btn-mini` exists in `app.css`):
```tsx
        {onClearRange && (
          <button type="button" className="btn-mini" data-testid="snap-clear-btn" onClick={onClearRange}>
            Clear
          </button>
        )}
```
In `BrDetailPage.tsx` line 653: `{id && <SnapshotPanel brId={id} range={range} onClearRange={() => setRange(null)} />}`.

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/components/SnapshotPanel.test.tsx`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/components/SnapshotPanel.tsx frontend/src/views/BrDetailPage.tsx frontend/src/components/SnapshotPanel.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(snapshot): add Clear button to clear the selected range' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 10: Synchronised typed UTC snapshot-window inputs

**Files:**
- Modify `frontend/src/components/FleetGraph.tsx`: add an `epochToLocalInput` helper (~line 42), a `handleTimeInput` callback + two `datetime-local` inputs in `FleetGraphCore` (above `.fleet-controls`).
- Test `frontend/src/components/FleetSection.test.tsx`.

**Interfaces:** consumes `fleet.t_start`/`t_end`, `selectedRange`, `onSelectRange`, `isoToEpoch`. Two inputs (testids `snap-from-input`/`snap-to-input`) bounded to the battle window, prefilled from `selectedRange`; a valid edit (`from < to`, in bounds) calls `onSelectRange` — the SAME state the shift-drag band uses (two views of one `range`). Invalid/empty input is ignored. `datetime-local` value is tz-less `YYYY-MM-DDTHH:MM`; `isoToEpoch` treats tz-less strings as UTC.

- [ ] **Step 1: Write failing tests.** Add to `FleetSection.test.tsx` (`fleetWithData` has `t_start: 1000`, `t_end: 1010`; add `fireEvent` to the import if needed):
```tsx
  it('shows UTC datetime-local inputs bounded to the battle window', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    const from = screen.getByTestId('snap-from-input') as HTMLInputElement
    const to = screen.getByTestId('snap-to-input') as HTMLInputElement
    expect(from.min).toBe('1970-01-01T00:16:40')
    expect(from.max).toBe('1970-01-01T00:16:50')
    expect(to.min).toBe('1970-01-01T00:16:40')
    expect(to.max).toBe('1970-01-01T00:16:50')
  })

  it('editing a typed time input updates the snapshot range', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    vi.mocked(api.snapshot).mockResolvedValue({ from_ts: 1002, to_ts: 1008, rows: [] })
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('snap-from-input'), { target: { value: '1970-01-01T00:16:42' } })
    fireEvent.change(screen.getByTestId('snap-to-input'), { target: { value: '1970-01-01T00:16:48' } })
    await waitFor(() => expect(api.snapshot).toHaveBeenCalled())
  })

  it('ignores an invalid (from >= to) typed range', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('snap-to-input'), { target: { value: '1970-01-01T00:16:45' } })
    fireEvent.change(screen.getByTestId('snap-from-input'), { target: { value: '1970-01-01T00:16:48' } })
    expect(api.snapshot).not.toHaveBeenCalled()
  })
```

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/components/FleetSection.test.tsx` → no `snap-from-input`.

- [ ] **Step 3: Implement.** Helper near the top-level helpers:
```tsx
function epochToLocalInput(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 16)
}
```
In `FleetGraphCore` (already destructures `fleet`, `selectedRange`, `onSelectRange`):
```tsx
  const winMin = fleet.t_start
  const winMax = fleet.t_end
  const handleTimeInput = useCallback(
    (which: 'from' | 'to', value: string) => {
      if (!value) return
      const epoch = isoToEpoch(value)
      if (!Number.isFinite(epoch)) return
      const cur = selectedRange ?? { from: winMin ?? epoch, to: winMax ?? epoch }
      const next = which === 'from' ? { from: epoch, to: cur.to } : { from: cur.from, to: epoch }
      if (next.from >= next.to) return
      if (winMin != null && (next.from < winMin || next.to < winMin)) return
      if (winMax != null && (next.from > winMax || next.to > winMax)) return
      onSelectRange(next)
    },
    [selectedRange, winMin, winMax, onSelectRange],
  )
```
Render the input row immediately before `<div className="fleet-controls">`:
```tsx
      <div className="fleet-time-inputs" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
        <label className="dim" style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.75rem' }}>
          Snapshot from (UTC)
          <input type="datetime-local" data-testid="snap-from-input" step={1}
            min={winMin != null ? epochToLocalInput(winMin) : undefined}
            max={winMax != null ? epochToLocalInput(winMax) : undefined}
            value={selectedRange ? epochToLocalInput(selectedRange.from) : ''}
            onChange={(e) => handleTimeInput('from', e.target.value)} />
        </label>
        <label className="dim" style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.75rem' }}>
          to (UTC)
          <input type="datetime-local" data-testid="snap-to-input" step={1}
            min={winMin != null ? epochToLocalInput(winMin) : undefined}
            max={winMax != null ? epochToLocalInput(winMax) : undefined}
            value={selectedRange ? epochToLocalInput(selectedRange.to) : ''}
            onChange={(e) => handleTimeInput('to', e.target.value)} />
        </label>
      </div>
```

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/components/FleetSection.test.tsx`, then `npm test`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/components/FleetGraph.tsx frontend/src/components/FleetSection.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(fleet-graph): synchronised UTC datetime-local snapshot window inputs' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

# Phase 3 — Hover summary (§3)

Backend precomputes per-bucket `leaders[]` aligned to `x`; frontend renders a DOM-overlay tooltip from `leaders[idx]`. Centre on **receivers** (top damage taken, top reps received) with dealer/repper secondary.

### Task 11: Backend `Leaders` model + per-bucket computation

**Files:**
- Modify `app/analytics/fleet.py` — add `LeaderEntry`/`Leaders` dataclasses (after `FleetSeriesOut`, ~line 334), a `leaders` field on `FleetTimeline` (~line 354-371), `_resolve_char_ships` + `_compute_leaders` helpers, and a `settings` kwarg on `fleet_timeline`.
- Test `tests/test_e3_fleet_timeline.py` (reuse `_make_br_with_fight`, `_insert_bucket`, `CHAR_A`/`CHAR_B`, `BUCKET_TS_1`).

**Interfaces:**
```python
@dataclass
class LeaderEntry:
    name: str
    ship: str | None
    amount: float

@dataclass
class Leaders:
    top_dmg_taken: LeaderEntry | None
    top_rep_recv: LeaderEntry | None
    top_dmg_dealt: LeaderEntry | None
    top_rep_done: LeaderEntry | None
```
`FleetTimeline` gains `leaders: list[Leaders]` (aligned index-for-index to `x`; `[]` when no logs). Per bucket: max incoming damage (`effect=='damage'`,`in`) → `top_dmg_taken`; max incoming rep (`effect.startswith('rep')`,`in`) → `top_rep_recv`; max outgoing damage → `top_dmg_dealt`; max outgoing rep → `top_rep_done`. Names via `_resolve_char_names`; ship via `_resolve_char_ships` (killmail hull, capsule-skipped, mirrors `composition.py`). Signature: `_compute_leaders(session, fight_ids, x, x_index, settings) -> list[Leaders]`.

- [ ] **Step 1: Write failing tests.** Add to `tests/test_e3_fleet_timeline.py`:
```python
async def test_leaders_per_bucket_picks_max_character(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    from app.db.models import Character
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        session.add(Character(character_id=CHAR_A, name="Alice", last_seen_at=dt.datetime.now(dt.UTC)))
        session.add(Character(character_id=CHAR_B, name="Bob", last_seen_at=dt.datetime.now(dt.UTC)))
        await session.flush()
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "in", 100.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "in", 300.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 500.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "damage", "out", 50.0, 1)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "rep_armor", "in", 200.0, 1)
        await _insert_bucket(session, fight_id, CHAR_B, BUCKET_TS_1, "rep_shield", "out", 150.0, 1)
        await session.commit()
    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())
    idx = tl.x.index(int(BUCKET_TS_1.timestamp()))
    ld = tl.leaders[idx]
    assert ld.top_dmg_taken.name == "Bob" and ld.top_dmg_taken.amount == pytest.approx(300.0)
    assert ld.top_dmg_dealt.name == "Alice" and ld.top_dmg_dealt.amount == pytest.approx(500.0)
    assert ld.top_rep_recv.name == "Alice" and ld.top_rep_recv.amount == pytest.approx(200.0)
    assert ld.top_rep_done.name == "Bob" and ld.top_rep_done.amount == pytest.approx(150.0)


async def test_leaders_aligned_to_x_and_null_when_absent(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    async with db_session_maker() as session:
        br_id, fight_id = await _make_br_with_fight(session)
        await _insert_bucket(session, fight_id, CHAR_A, BUCKET_TS_1, "damage", "out", 100.0, 1)
        await session.commit()
    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())
    assert len(tl.leaders) == len(tl.x)
    ld = tl.leaders[tl.x.index(int(BUCKET_TS_1.timestamp()))]
    assert ld.top_dmg_dealt is not None
    assert ld.top_dmg_taken is None and ld.top_rep_recv is None and ld.top_rep_done is None


async def test_leaders_empty_for_no_logs(db_session_maker) -> None:  # type: ignore[no-untyped-def]
    from app.analytics.fleet import fleet_timeline
    from app.config import get_settings
    async with db_session_maker() as session:
        br_id, _ = await _make_br_with_fight(session)
        await session.commit()
    async with db_session_maker() as session:
        tl = await fleet_timeline(session, br_id, settings=get_settings())
    assert tl.leaders == []
```

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_e3_fleet_timeline.py -k leaders -q` → no `settings` kwarg / no `leaders`.

- [ ] **Step 3: Implement.** Add the two dataclasses after `FleetSeriesOut`; add `leaders: list[Leaders]` to `FleetTimeline` right after `series`. Add `KillmailAttacker` to the model imports. Add `_resolve_char_ships` (build `character_id → hull name` from `FightKill`→`Killmail`/`KillmailAttacker`→`InventoryType`, victim hull first then attacker ship, skip capsules) and `_compute_leaders` (group `LogEventBucket` by `(bucket_ts, character_id, effect_type, direction)` summing `abs(sum_amount)`, bucket via `x_index`, classify into the four metrics, resolve names+ships, pick max per metric per bucket; return `[]` when no rows). Add `settings: Settings | None = None` to `fleet_timeline`; after the `series` list is built call `_compute_leaders(session, fight_ids, x, x_index, settings or get_settings())`; populate `leaders=[]` in the empty early-return and `leaders=leaders` in the final return. (Full helper bodies are in the design recon for §3; follow the metric classification: `damage/in→dmg_taken`, `damage/out→dmg_dealt`, `rep*/in→rep_recv`, `rep*/out→rep_done`.)

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_e3_fleet_timeline.py -q` (whole file — existing callers pass no `settings`, so the kwarg MUST default to `None`).

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/fleet.py tests/test_e3_fleet_timeline.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(fleet): per-bucket leaders (top dmg/rep receiver+dealer) aligned to x' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 12: Expose `leaders[]` on the fleet-timeline endpoint

**Files:**
- Modify `app/api/schemas.py` — add `LeaderEntryOut`/`LeadersOut`, `leaders` on `FleetTimelineOut` (~lines 374-384).
- Modify `app/api/fleet.py` — import the schemas, add a `_leader_out` helper, map `tl.leaders` in `get_fleet_timeline`.
- Test `tests/test_e3_fleet_timeline.py` — API-shape test.

**Interfaces:** `LeaderEntryOut{name,ship,amount}`, `LeadersOut{top_dmg_taken,top_rep_recv,top_dmg_dealt,top_rep_done}`; `FleetTimelineOut.leaders: list[LeadersOut]`.

- [ ] **Step 1: Write failing test.** Add the `tmp_path/monkeypatch` API test (mirroring the existing `test_api_fleet_timeline_*`): seed a BR+fight + a `Character(Alice)` + one `damage/in` bucket, GET `/api/brs/{br_id}/fleet-timeline` with creator headers, assert `len(data["leaders"]) == len(data["x"])` and `data["leaders"][idx]["top_dmg_taken"]["name"] == "Alice"` and `top_rep_recv is None`.

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_e3_fleet_timeline.py -k includes_leaders -q` → no `leaders` key.

- [ ] **Step 3: Implement.** Add the two Pydantic models before `FleetTimelineOut` and the `leaders: list[LeadersOut]` field. In `app/api/fleet.py` add:
```python
def _leader_out(e):  # type: ignore[no-untyped-def]
    from app.analytics.fleet import LeaderEntry
    if e is None:
        return None
    assert isinstance(e, LeaderEntry)
    return LeaderEntryOut(name=e.name, ship=e.ship, amount=e.amount)
```
and in the `FleetTimelineOut(...)` return add the mapped `leaders=[LeadersOut(top_dmg_taken=_leader_out(ld.top_dmg_taken), …) for ld in tl.leaders]`.

- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_e3_fleet_timeline.py -q`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/api/schemas.py app/api/fleet.py tests/test_e3_fleet_timeline.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(api): expose fleet leaders[] on fleet-timeline endpoint' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 13: Frontend `Leaders` types + `hoverSummaryPlugin`

**Files:**
- Modify `frontend/src/api.ts` — add `LeaderEntry`/`Leaders` interfaces + `leaders` on `FleetTimeline`.
- Create `frontend/src/hoverSummary.ts` — pure `renderHoverSummary(view, leaders, idx): string`.
- Create `frontend/src/hoverSummary.test.ts`.
- Modify `frontend/src/components/FleetGraph.tsx` — add `hoverSummaryPlugin(view, leaders)` (mirror `killMarkersPlugin`'s DOM-overlay tip), thread `view`+`fleet.leaders` into `PanelChart`, append to the `plugins` array; add a `.hover-tip` rule to `frontend/src/styles/app.css`.

**Interfaces:** `LeaderEntry{name:string;ship:string|null;amount:number}`, `Leaders{top_dmg_taken,top_rep_recv,top_dmg_dealt,top_rep_done: LeaderEntry|null}`; `FleetTimeline.leaders: Leaders[]`. `renderHoverSummary` returns innerHTML: side totals (DPS out, damage taken, reps received from the `FleetView` family series at `idx`) + prominent `top_dmg_taken`/`top_rep_recv` (class `hover-tip-top`) + smaller `top_dmg_dealt`/`top_rep_done` (class `hover-tip-secondary`); a `"no log data"` line when `leaders[idx]` is missing/all-null. **Confirm the compact number formatter name in `format.ts` and use it** (per Global Constraints).

- [ ] **Step 1: Write failing test.** Create `frontend/src/hoverSummary.test.ts` building a `FleetTimeline` with two buckets — first populated leaders, second all-null — running it through `toFleetView`, asserting: bucket 0 HTML contains the top receiver name+amount and `hover-tip-top`; contains `hover-tip-secondary` + a dealer name; bucket 1 and an out-of-range idx contain `"no log data"`.

- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/hoverSummary.test.ts` → module missing / `Leaders` not exported.

- [ ] **Step 3: Implement.** Add the interfaces to `api.ts`. Create `hoverSummary.ts` as a pure HTML builder (no uPlot import) that reads side totals from `view.panels[*].series` (`dmg_out`/`dmg_in`/`rep_in` magnitudes at `idx`) and the four leader entries, HTML-escaping names. Then in `FleetGraph.tsx` add `hoverSummaryPlugin(view, leaders)` modelled on `killMarkersPlugin`: a `tip` div (class `hover-tip`) on `document.body`, `setCursor` hook reads `u.cursor.idx`, sets `tip.innerHTML = renderHoverSummary(view, leaders, idx)` + positions at the cursor (hide on `idx == null`), removed in `destroy`. Thread `view`/`fleet.leaders` through `PanelChartProps`/`CoreProps` and append the plugin to the `plugins` array. Add a `.hover-tip` CSS rule mirroring `.kill-tip`.

- [ ] **Step 4: Run, expect PASS.** `npm test -- src/hoverSummary.test.ts`, then `npm test`.

- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/api.ts frontend/src/hoverSummary.ts frontend/src/hoverSummary.test.ts frontend/src/components/FleetGraph.tsx frontend/src/styles/app.css
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(fleet-graph): hover-summary tooltip (top receivers + side totals)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

# Phase 4 — Killmail augmentation (§5)

All items work from killmails alone and enrich when logs exist. Backend SQLAlchemy style mirrors `composition.py`/`reconcile.py`. **Important constraint:** `InventoryType` stores only `name`/`group_id`/`group_name`/`category_id`/`category_name` — no range attributes — so weapon roles are name/group-driven via `classify_weapon`. There is **no per-item price source** in the codebase, so item-loss ISK value is `None` (counts only) unless a price table is later added.

### Task 14: Persist victim `damage_taken` (effective tank)

**Files:**
- Modify `app/killmail/parse.py` (`ParsedVictim` lines 35-39; victim construction lines 109-122).
- Modify `app/db/models.py` (`Killmail`, after `hash`).
- Modify `app/ingest/persist.py` (km_rows dict, lines 240-254).
- Test `tests/test_killmail_parse.py` (new) + `tests/test_persist_damage_taken.py` (new).

**Interfaces:** `ParsedVictim.damage_taken: int = 0` from `raw["victim"]["damage_taken"]`; `Killmail.damage_taken: Mapped[int | None]`; persisted.

- [ ] **Step 1: Write failing parse test** `tests/test_killmail_parse.py`:
```python
from app.killmail.parse import parse_killmail

def _raw(**v):
    return {"killmail_id": 1, "killmail_time": "2026-06-10T20:00:00Z", "solar_system_id": 31002222,
            "victim": {"ship_type_id": 645, "damage_taken": 51234, **v}, "attackers": [], "zkb": {}}

def test_victim_damage_taken_parsed():
    assert parse_killmail(_raw()).victim.damage_taken == 51234

def test_victim_damage_taken_defaults_zero():
    raw = _raw(); del raw["victim"]["damage_taken"]
    assert parse_killmail(raw).victim.damage_taken == 0
```
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_killmail_parse.py -x -q`.
- [ ] **Step 3: Implement parse.** `ParsedVictim`: add `damage_taken: int = 0`. Victim construction: `damage_taken=int(_d(victim_raw, "damage_taken", 0)),`.
- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_killmail_parse.py -x -q`.
- [ ] **Step 5: Write failing persist test** `tests/test_persist_damage_taken.py`:
```python
import pytest
from sqlalchemy import select
from app.db.models import Killmail
from app.ingest.persist import persist_killmails

@pytest.mark.asyncio
async def test_persist_writes_damage_taken(db_session_maker):
    raw = {"killmail_id": 77, "killmail_time": "2026-06-10T20:00:00Z", "solar_system_id": 31002222,
           "victim": {"ship_type_id": 645, "damage_taken": 99999}, "attackers": [], "zkb": {}}
    async with db_session_maker() as s:
        await persist_killmails(s, [raw], names={}, values=None)
        await s.commit()
    async with db_session_maker() as s:
        dt_ = (await s.execute(select(Killmail.damage_taken).where(Killmail.killmail_id == 77))).scalar_one()
    assert dt_ == 99999
```
(Confirm the real `persist_killmails` signature and adapt the call if it differs.)
- [ ] **Step 6: Run, expect FAIL.** `uv run pytest tests/test_persist_damage_taken.py -x -q`.
- [ ] **Step 7: Implement column + persist.** `Killmail`: `damage_taken: Mapped[int | None] = mapped_column(Integer, nullable=True)`. `persist.py` km_rows: `"damage_taken": km.victim.damage_taken,`.
- [ ] **Step 8: Run, expect PASS.** `uv run pytest tests/test_killmail_parse.py tests/test_persist_damage_taken.py -x -q`.
- [ ] **Step 9: Commit** (note: prod needs `ALTER TABLE killmail ADD COLUMN damage_taken INTEGER`).
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/killmail/parse.py app/db/models.py app/ingest/persist.py tests/test_killmail_parse.py tests/test_persist_damage_taken.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): persist victim damage_taken (effective tank)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 15: Per-loss damage attribution analytics + API

**Files:**
- Create `app/analytics/damage_attribution.py`.
- Modify `app/api/schemas.py` (append after `CompositionOut`).
- Modify `app/api/brs.py` (new endpoint near `get_br`).
- Test `tests/test_damage_attribution.py`.

**Interfaces:**
```python
@dataclass
class AttackerDamageRow:
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    final_blow: bool

@dataclass
class LossDamageAttribution:
    killmail_id: int
    damage_taken: int | None
    total_attributed: int
    attackers: list[AttackerDamageRow]   # sorted by damage_done desc; share = damage_done/total (0.0 if total 0)
```
`loss_damage_attribution(session, killmail_id) -> LossDamageAttribution`. Pydantic `AttackerDamageRowOut`/`LossDamageAttributionOut`. Endpoint `GET /api/brs/{br_id}/losses/{killmail_id}/damage` → 404 if the killmail is not in the BR (guard via the BR↔fight↔killmail join used in `composition.py`).

- [ ] **Step 1: Write failing analytics test** `tests/test_damage_attribution.py`. Seed a BR+fight via the existing helper, insert a `Killmail` (with `damage_taken`) linked via `FightKill`, and two `KillmailAttacker` rows:
```python
import pytest
from sqlalchemy import select
from app.analytics.damage_attribution import loss_damage_attribution
from app.db.models import Killmail, KillmailAttacker, FightKill

async def _seed_loss(session, fight_id, km_id=900):
    session.add(Killmail(killmail_id=km_id, victim_character_id=42, victim_ship_type_id=645,
                         total_value=1.0, damage_taken=4000))
    session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=0, character_id=10,
                                 ship_type_id=640, damage_done=3000, final_blow=False))
    session.add(KillmailAttacker(killmail_id=km_id, attacker_idx=1, character_id=11,
                                 ship_type_id=641, damage_done=1000, final_blow=True))
    session.add(FightKill(fight_id=fight_id, killmail_id=km_id))
    await session.flush()
    return km_id

@pytest.mark.asyncio
async def test_ranked_with_share_and_final_blow(db_session_maker):
    from tests.test_e3_fleet_timeline import _make_br_with_fight  # reuse helper
    async with db_session_maker() as s:
        _, fight_id = await _make_br_with_fight(s)
        km_id = await _seed_loss(s, fight_id)
        await s.commit()
    async with db_session_maker() as s:
        res = await loss_damage_attribution(s, km_id)
    assert [r.damage_done for r in res.attackers] == [3000, 1000]
    assert res.total_attributed == 4000
    assert res.damage_taken == 4000
    assert abs(res.attackers[0].share - 0.75) < 1e-6
    assert next(r for r in res.attackers if r.damage_done == 1000).final_blow is True
```
(Confirm the exact `Killmail`/`KillmailAttacker`/`FightKill` required columns from `app/db/models.py` and the BR↔fight↔killmail link table name; adjust the import of `_make_br_with_fight` if it lives elsewhere.)

- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_damage_attribution.py -x -q`.
- [ ] **Step 3: Implement analytics.** `loss_damage_attribution`: select `KillmailAttacker` for the km, join `Character` for names, read `Killmail.damage_taken`, compute total + per-row share, sort desc.
- [ ] **Step 4: Run, expect PASS.** `uv run pytest tests/test_damage_attribution.py -x -q`.
- [ ] **Step 5: Write failing API test.** Mirror an existing contract test (TestClient + member headers): seed a BR+loss, GET `/api/brs/{br_id}/losses/{km}/damage`, assert JSON shape + share, and 404 for a killmail not in that BR.
- [ ] **Step 6: Run, expect FAIL.** `uv run pytest tests/test_damage_attribution.py -x -q`.
- [ ] **Step 7: Implement schema + endpoint.** Add `AttackerDamageRowOut`/`LossDamageAttributionOut`; add the endpoint in `app/api/brs.py` guarding km-in-BR (else 404).
- [ ] **Step 8: Run, expect PASS.** `uv run pytest tests/test_damage_attribution.py -x -q`.
- [ ] **Step 9: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/damage_attribution.py app/api/schemas.py app/api/brs.py tests/test_damage_attribution.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): per-loss damage attribution analytics + API' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 16: Battle-level damage leaderboard analytics + API

**Files:** modify `app/analytics/damage_attribution.py` (add `br_damage_leaderboard`), `app/api/schemas.py`, `app/api/brs.py`; extend `tests/test_damage_attribution.py`.

**Interfaces:**
```python
@dataclass
class LeaderboardRow:
    character_id: int | None
    character_name: str | None
    damage_done: int
    share: float
    log_damage_out: float | None  # None unless logs present (filled in Task 21)

@dataclass
class BrDamageLeaderboard:
    rows: list[LeaderboardRow]      # sorted by damage_done desc
    total_attributed: int
    logs_present: bool
```
`br_damage_leaderboard(session, br_id) -> BrDamageLeaderboard`. Gather all `FightKill.killmail_id` for the BR's fights (verbatim BR↔fight join from `composition.py`), sum `KillmailAttacker.damage_done` grouped by `character_id`, name lookup, `share = damage_done/grand_total`. `log_damage_out` stays `None`/`logs_present=False` here (Task 21 wires the overlay). Endpoint `GET /api/brs/{br_id}/damage-leaderboard` (404 if BR missing).

- [ ] **Step 1: Write failing test** (seed BR with ≥2 fights/kills, multiple attackers): assert sums across kills, `share` sums ≈1.0, sorted desc, `logs_present is False`.
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_damage_attribution.py -k leaderboard -x -q`.
- [ ] **Step 3: Implement** `br_damage_leaderboard` (km_ids via the `composition.py` BR↔fight join; group-sum; name lookup).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Write failing API test** (shape + `logs_present` + 404 unknown BR).
- [ ] **Step 6: Run, expect FAIL.**
- [ ] **Step 7: Implement schema + endpoint.**
- [ ] **Step 8: Run, expect PASS.** `uv run pytest tests/test_damage_attribution.py -x -q`.
- [ ] **Step 9: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/damage_attribution.py app/api/schemas.py app/api/brs.py tests/test_damage_attribution.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): BR damage leaderboard analytics + API' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 17: Weapon-role mapping helper

**Files:** create `app/analytics/weapon_roles.py`; test `tests/test_weapon_roles.py`.

**Interfaces:**
```python
@dataclass
class WeaponTypeInfo:
    type_id: int
    name: str | None
    group_name: str | None
    category_id: int

@dataclass
class WeaponRole:
    role: str   # turret|missile|drone|smartbomb|ewar|tackle|other
    band: str   # short|medium|long|none

def weapon_role(info: WeaponTypeInfo) -> WeaponRole: ...
```
Reuse `classify_weapon` (`app/analytics/weapons.py`) for turret/missile/smartbomb families; category_id 18 → drone (checked first); group-name keywords → tackle (`Warp Scrambler`, `Warp Disruptor`, `Stasis Web`) and ewar (`ECM`, `Sensor Dampener`, `Target Painter`, `Weapon Disruptor`); else `other`.

- [ ] **Step 1: Write failing test** `tests/test_weapon_roles.py`:
```python
from app.analytics.weapon_roles import weapon_role, WeaponTypeInfo

def test_railgun_is_turret():
    assert weapon_role(WeaponTypeInfo(3074, "250mm Railgun II", "Hybrid Weapon", 7)).role == "turret"
def test_heavy_missile_is_missile():
    assert weapon_role(WeaponTypeInfo(2410, "Heavy Missile Launcher II", "Missile Launcher Heavy", 7)).role == "missile"
def test_drone_by_category_18():
    assert weapon_role(WeaponTypeInfo(2486, "Warrior II", "Combat Drone", 18)).role == "drone"
def test_smartbomb():
    assert weapon_role(WeaponTypeInfo(0, "Large EMP Smartbomb II", "Smart Bomb", 7)).role == "smartbomb"
def test_scram_is_tackle():
    assert weapon_role(WeaponTypeInfo(0, "Warp Scrambler II", "Warp Scrambler", 7)).role == "tackle"
def test_web_is_tackle():
    assert weapon_role(WeaponTypeInfo(0, "Stasis Webifier II", "Stasis Web", 7)).role == "tackle"
def test_ecm_is_ewar():
    assert weapon_role(WeaponTypeInfo(0, "Multispectral ECM II", "ECM", 7)).role == "ewar"
def test_unknown_is_other():
    assert weapon_role(WeaponTypeInfo(0, "Some Weird Thing", "Mystery", 0)).role == "other"
```
(Verify `classify_weapon`'s real return shape and align the turret/missile/smartbomb mapping to it.)
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_weapon_roles.py -x -q`.
- [ ] **Step 3: Implement** `app/analytics/weapon_roles.py`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/weapon_roles.py tests/test_weapon_roles.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): weapon-role/band mapping (reuses classify_weapon)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 18: Composition augmentation — weapons/effects per pilot

**Files:** modify `app/analytics/composition.py` (`CompositionPilot` + attacker query), `app/api/schemas.py` (`CompositionPilotOut`), `app/api/fleet.py` (composition mapping), `frontend/src/api.ts` (`CompositionPilot`), `frontend/src/components/FleetsPanel.tsx` (`PilotRow`); tests `tests/test_composition.py` + `frontend/src/components/FleetsPanel.test.tsx`.

**Interfaces (keep ship-centric layout; ADD per-pilot weapons):** new `CompositionPilot.weapons: list[WeaponEffect]` where `WeaponEffect(type_id:int, name:str, role:str)` deduped by `type_id`; Pydantic `WeaponEffectOut` + `CompositionPilotOut.weapons: list[WeaponEffectOut] = []`; TS `interface WeaponEffect { type_id:number; name:string; role:string }` + `CompositionPilot.weapons: WeaponEffect[]`. Weapon ids from `KillmailAttacker.weapon_type_id`, resolved via `InventoryType.name` + `weapon_role()`.

- [ ] **Step 1: Write failing backend test** in `tests/test_composition.py`: seed an attacker with a `weapon_type_id` + its `InventoryType`, assert the pilot row exposes `weapons` containing that type_id + role.
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_composition.py -x -q`.
- [ ] **Step 3: Implement backend.** In the attacker loop also collect `weapon_type_id` per char; extend the `InventoryType` id set to include weapon ids; add `weapons` to the dataclass + each `CompositionPilot`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: API contract + schema/mapping.** Add `WeaponEffectOut` + `CompositionPilotOut.weapons`; map in `app/api/fleet.py`. Extend the composition contract test to assert `pilots[*]["weapons"]`. Run FAIL → implement → PASS.
- [ ] **Step 6: Write failing frontend test** in `FleetsPanel.test.tsx`: add `weapons: [{type_id:3074,name:'Railgun',role:'turret'}]` to a mock pilot, assert the chip renders. Run `npm test -- src/components/FleetsPanel.test.tsx` → FAIL.
- [ ] **Step 7: Implement frontend.** `api.ts` (`WeaponEffect` + `CompositionPilot.weapons`) and `FleetsPanel.tsx` `PilotRow` (render `p.weapons` as small role-tagged chips after the ship name; keep the ship-centric layout). Run → PASS.
- [ ] **Step 8: Run** backend `uv run pytest tests/test_composition.py -x -q` + `npm test`.
- [ ] **Step 9: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/composition.py app/api/schemas.py app/api/fleet.py frontend/src/api.ts frontend/src/components/FleetsPanel.tsx tests/test_composition.py frontend/src/components/FleetsPanel.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(composition): per-pilot identified weapons/effects from killmails' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 19: Item-loss slot breakdown analytics + API

**Files:** create `app/analytics/item_losses.py`; modify `app/api/schemas.py`, `app/api/brs.py`; test `tests/test_item_losses.py`.

**Interfaces:**
```python
@dataclass
class ItemLossRow:
    type_id: int
    name: str
    location: str
    qty_destroyed: int
    qty_dropped: int

@dataclass
class SlotLoss:
    location: str
    destroyed_qty: int
    dropped_qty: int
    value: float | None        # None — no per-item price source
    items: list[ItemLossRow]

@dataclass
class ItemLossBreakdown:
    killmail_id: int
    slots: list[SlotLoss]      # ordered: high, med, low, rig, subsystem, drone_bay, cargo, implant, other
```
`item_loss_breakdown(session, killmail_id) -> ItemLossBreakdown` grouping `KillmailItem` by `location` (the `flags.py` category), names via `InventoryType`. Endpoint `GET /api/brs/{br_id}/losses/{killmail_id}/items` (404 if km not in BR — reuse Task 15's guard).

- [ ] **Step 1: Write failing test** (insert a Killmail + KillmailItem rows across high/low/cargo with destroyed vs dropped qtys + InventoryType names): assert per-slot sums + item rows.
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_item_losses.py -x -q`.
- [ ] **Step 3: Implement analytics.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Write failing API test** (shape + 404 unrelated km). Run FAIL.
- [ ] **Step 6: Implement schema + endpoint.** Run PASS.
- [ ] **Step 7: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/item_losses.py app/api/schemas.py app/api/brs.py tests/test_item_losses.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): per-loss item slot breakdown (destroyed/dropped)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 20: Frontend panels — damage attribution, effective tank, item losses, leaderboard

**Files:** create `frontend/src/components/LossDetailPanel.tsx`, `frontend/src/components/DamageLeaderboard.tsx`; modify `frontend/src/api.ts` (types + `api.lossDamage`/`api.lossItems`/`api.damageLeaderboard`), `frontend/src/views/BrDetailPage.tsx` (mount the leaderboard in `br-col-side`); tests `LossDetailPanel.test.tsx`, `DamageLeaderboard.test.tsx`.

**Interfaces:** TS types mirror the Task 15/16/19 Pydantic field names exactly. `api.lossDamage(brId, kmId)`, `api.lossItems(brId, kmId)`, `api.damageLeaderboard(brId)`. `LossDetailPanel` props `{ brId: string; killmailId: number }` → ranked attackers (share %, final-blow highlighted), "absorbed {damage_taken} damage before dying", and a destroyed/dropped item table.

- [ ] **Step 1: Write failing `DamageLeaderboard.test.tsx`** (mock `api.damageLeaderboard`, assert rows render sorted with share %).
- [ ] **Step 2: Run, expect FAIL.** `npm test -- src/components/DamageLeaderboard.test.tsx`.
- [ ] **Step 3: Implement** `api.ts` types/fetchers + `DamageLeaderboard.tsx`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Write failing `LossDetailPanel.test.tsx`** (mock `api.lossDamage`+`api.lossItems`; assert "absorbed 51,234 damage", final-blow marker, share %, a destroyed/dropped item row).
- [ ] **Step 6: Run, expect FAIL.** `npm test -- src/components/LossDetailPanel.test.tsx`.
- [ ] **Step 7: Implement** `LossDetailPanel.tsx`; mount `DamageLeaderboard` in `BrDetailPage.tsx` `br-col-side`.
- [ ] **Step 8: Run, expect PASS.** `npm test`.
- [ ] **Step 9: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add frontend/src/api.ts frontend/src/components/LossDetailPanel.tsx frontend/src/components/DamageLeaderboard.tsx frontend/src/views/BrDetailPage.tsx frontend/src/components/LossDetailPanel.test.tsx frontend/src/components/DamageLeaderboard.test.tsx
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(fe): damage attribution, effective tank, item-loss, leaderboard panels' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

### Task 21: Augmentation seam — log overlay on the leaderboard

**Files:** modify `app/analytics/damage_attribution.py` (`br_damage_leaderboard`); extend `tests/test_damage_attribution.py`.

**Interfaces:** killmail-attributed damage stays authoritative ordering; `log_damage_out` is an overlay populated only where logs exist, via `fight_damage_reconcile` (`app/analytics/reconcile.py`) per fight. `BrDamageLeaderboard.logs_present=True` iff any fight had log rows; per-row `log_damage_out: float | None`.

- [ ] **Step 1: Write failing test:** (a) no logs → all rows `log_damage_out is None`, `logs_present is False`; (b) with `LogEvent` rows → matching char gets non-null `log_damage_out`, `logs_present is True`.
- [ ] **Step 2: Run, expect FAIL.** `uv run pytest tests/test_damage_attribution.py -k seam -x -q`.
- [ ] **Step 3: Implement** the overlay merge importing `fight_damage_reconcile` (do not re-aggregate logs by hand).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit.**
```bash
git -C /home/matron/dev/nv-wh-fight-history add app/analytics/damage_attribution.py tests/test_damage_attribution.py
git -C /home/matron/dev/nv-wh-fight-history commit -m "$(printf '%s\n\n%s' 'feat(killmail): augmentation seam — log overlay on leaderboard (reuses reconcile)' 'Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Final verification (after all tasks)

- Backend: `uv run pytest -q` ; `uv run ruff check app tests` ; `uv run mypy app`.
- Frontend: `cd frontend && npm test`.
- Backfill existing prod DB (one-time):
  1. Run the ALTER TABLE statements: `ALTER TABLE log_event ADD COLUMN source_name VARCHAR(128); ALTER TABLE log_event ADD COLUMN target_name VARCHAR(128); ALTER TABLE log_event ADD COLUMN authoritative BOOLEAN DEFAULT 0; ALTER TABLE log_event ADD COLUMN dedupe_suppressed BOOLEAN DEFAULT 0; ALTER TABLE killmail ADD COLUMN damage_taken INTEGER;`
  2. Run `python -m app.logs.reparse` — this replays gamelogs and backfills the **LogEvent** columns (`source_name`, `target_name`, `authoritative`, `dedupe_suppressed`). It does **NOT** backfill `Killmail.damage_taken`; that column is only populated on killmail re-ingest (e.g. re-running the ESI/zKB ingest pipeline for those killmails).

## Self-review notes (for the executor)

- **Type consistency** verified across the §3 chain (`LeaderEntry{name,ship,amount}` / `Leaders{top_dmg_taken,top_rep_recv,top_dmg_dealt,top_rep_done}` identical in dataclass → `LeadersOut` → TS) and the §5 chain (analytics dataclasses ↔ `*Out` schemas ↔ TS fetchers share field names).
- **Verify-before-coding hooks** intentionally left for the executor (they require reading the real files): the compact number formatter name in `format.ts`; `classify_weapon`'s return shape; `persist_killmails` signature; the exact BR↔fight↔killmail link-table name; and that `_make_br_with_fight`/`_insert_fight`/`_insert_bucket` live where the tests import them from. These are noted inline at each use; resolve them from the source, do not guess.
- **Schema changes are batched** into Tasks 2 and 14 only; both carry the create_all/ALTER note.
