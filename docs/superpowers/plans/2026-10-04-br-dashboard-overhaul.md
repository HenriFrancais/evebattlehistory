# Battle Report Dashboard Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the battle report page into a wide tabbed dashboard with one switchable timeline chart, a linked per-pilot stats table with isolation, an Involved tab grouped by alliance and corp, and corp/alliance tickers on every pilot name.

**Architecture:** The backend gains miss events, per-bucket min/max, stored tickers and two read endpoints (`/pilot-timeline`, `/entities`). The frontend loads per-pilot 5-second buckets once and computes range stats, isolation and stat-type switching in the browser with pure functions; only a row's expanded breakdown calls the server.

**Tech Stack:** FastAPI, SQLAlchemy (async, SQLite), pytest; React 18, react-router 6, uPlot, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-04-br-dashboard-overhaul-design.md`

## Global Constraints

- Per-pilot log data for other people's characters is FC/HC-only, enforced in the API (`viewer_scope`), never in the browser.
- Dates are ISO `YYYY-MM-DD`, times 24h UTC, never locale-formatted.
- Schema changes go in `app/db/migrate.py` as a new idempotent migration; no hand `ALTER`.
- Parser changes take effect on old logs only after `python -m app.logs.reparse`; upload and reparse share `build_event_rows`.
- Read endpoints must not call ESI or commit.
- Existing dark theme and colour tokens in `frontend/src/styles/app.css` are kept; no new fonts or palette.
- Checks that must pass: `uv run pytest`, `uv run ruff check app tests`, `uv run mypy app`, and in `frontend/`: `npm test`, `npm run build`.
- One commit per task, on `feat/br-dashboard-overhaul`. Nothing is pushed.

## Review Focus

1. A miss event leaking into a damage total, hit count, hit-quality mix, reconcile row or broadcast metric. Pinned in Task 1 (`test_miss_excluded_from_aggregates`).
2. A member receiving another pilot's series from `/pilot-timeline`. Pinned in Task 3 (`test_pilot_timeline_member_sees_only_own`).
3. A pilot name containing markup reaching an HTML-string tooltip unescaped once tickers are appended. Pinned in Task 6 (`pilotLabel escapes name and tickers`).
4. A selected range that contains no buckets, or one clipped to a partial bucket, producing `NaN`, `Infinity` or a negative average. Pinned in Task 7 (`pilotStats` empty and zero-length range cases).
5. A battle whose logs predate the reparse (no miss rows, null bucket min/max) rendering zeros that read as real values. Pinned in Task 7 (`null min/max renders as blank`) and Task 9 (table cell test).

---

## File Structure

**Backend**

| File | Change |
| --- | --- |
| `app/logs/parse.py` | `_match_miss`; wired into `parse_line` |
| `app/logs/associate.py` | bucket rebuild writes `min_amount`, `max_amount` |
| `app/db/models.py` | `Corporation.ticker`, `Alliance.ticker`, `LogEventBucket.min_amount/max_amount` |
| `app/db/migrate.py` | migration 5 |
| `app/esi/client.py` | `fetch_tickers` |
| `app/ingest/tickers.py` (new) | `fill_missing_tickers`, CLI `python -m app.ingest.tickers` |
| `app/ingest/pipeline.py` | call `fill_missing_tickers` after persist |
| `app/analytics/pilot_timeline.py` (new) | `pilot_timeline` |
| `app/analytics/entities.py` (new) | `br_entities` |
| `app/analytics/fleet.py` | snapshot rows gain hit stats |
| `app/analytics/composition.py` | pilot corp/alliance ids; side losses and ISK lost |
| `app/api/fleet.py`, `app/api/schemas.py` | new endpoints and fields |

**Frontend** (`frontend/src/`)

| File | Change |
| --- | --- |
| `api.ts` | new types and calls |
| `cache.ts` | loaders for pilot timeline and entities |
| `entities.tsx` (new) | directory index, context, `pilotLabel` |
| `components/PilotName.tsx` (new) | name with tickers |
| `pilotStats.ts` (new) | range stats and summed series |
| `involved.ts` (new) | alliance/corp grouping |
| `components/chartPlugins.ts` (new) | uPlot plugins moved from `FleetGraph.tsx` |
| `components/TimelineChart.tsx` (new) | single chart |
| `components/StatsTable.tsx`, `PilotBreakdown.tsx` (new) | table and expanded row |
| `components/InvolvedSide.tsx` (new) | one side's grouped list |
| `components/BrHeader.tsx`, `SourcesPanel.tsx` (new) | split out of `BrDetailPage.tsx` |
| `views/br/{Involved,Timeline,Comms,Aar,Manage}Tab.tsx` (new) | tabs |
| `views/BrDetailPage.tsx` | shell |
| `App.tsx` | tab routes, character redirect |
| `fleet.ts`, `hoverSummary.ts` | four families; ticker labels |
| `styles/app.css` | shell, tabs, table, involved |
| removed | `FleetGraph.tsx`, `SnapshotPanel.tsx`, `FleetsPanel.tsx`, `views/CharacterTimelinePage.tsx`, their tests |

---

### Task 1: Miss events and bucket min/max

**Files:** Modify `app/logs/parse.py`, `app/logs/associate.py`, `app/db/models.py`, `app/db/migrate.py`. Test `tests/test_log_parse.py`, `tests/test_association.py`, `tests/test_migrations.py`, new `tests/test_miss_events.py`.

**Interfaces — Produces:**
- `LogEvent` rows with `effect_type="miss"`, `direction` in/out, `amount=None`, `quality="Misses"`, `other_name`, `module_name`.
- `LogEventBucket.min_amount: float | None`, `max_amount: float | None` — smallest and largest positive `abs(amount)` in the bucket; `None` when no positive amount.
- `MISS_EFFECT = "miss"` exported from `app/logs/parse.py`.

Miss line shapes (after `strip_eve_markup`):

```
<attacker> misses you completely - <module>                      → in,  other=<attacker>
<drone> belonging to <attacker> misses you completely - <module> → in,  other=<attacker>
Your <module> misses <target> completely - <module>              → out, other=<target>
Your group of <module> misses <target> completely - <module>     → out, other=<target>
```

- [ ] **Step 1:** Write failing parser tests for the four shapes plus a non-miss line containing the word "misses" in a pilot name position that must not match (`"432 from Misses Mcgee[.TST](Rifter) - 125mm - Hits"` stays damage).
- [ ] **Step 2:** Run `uv run pytest tests/test_log_parse.py -k miss -q`; expect failures.
- [ ] **Step 3:** Implement `_match_miss` with two anchored regexes and call it after `_match_damage` in the `combat` branch:

```python
_MISS_IN_RE = re.compile(r"^(?:.+? belonging to )?(.+?) misses you completely(?:\s+-\s+(.+))?$")
_MISS_OUT_RE = re.compile(r"^Your (?:group of )?.+? misses (.+?) completely(?:\s+-\s+(.+))?$")
```

- [ ] **Step 4:** Write `tests/test_miss_events.py::test_miss_excluded_from_aggregates`: ingest `tests/fixtures/gamelogs/full_fight.txt` through the existing association fixture; assert the fleet timeline has no `miss:*` series, `fleet_snapshot` returns no row with `effect_type == "miss"`, and damage totals equal the pre-change totals computed from damage rows only. Grep every reader of `LogEvent.effect_type` / `LogEventBucket.effect_type` and add an explicit known-effect filter to any that lacks one (`app/api/timeline.py`, `app/analytics/reconcile.py`, `app/analytics/ewar.py`, `app/analytics/performance.py`, `app/analytics/broadcasts.py`, `app/analytics/composition.py`, `app/logs/coverage.py`).
- [ ] **Step 5:** Bucket test: two damage events of 100 and 40 and one miss in one bucket → damage bucket `min_amount == 40`, `max_amount == 100`, `event_count == 2`; miss bucket `event_count == 1`, min/max `None`.
- [ ] **Step 6:** Add model columns, migration 5 (`add_column_if_missing` ×4), and the rebuild change. Migration test: a v4 database migrates to v5 with the four columns present.
- [ ] **Step 7:** Run the full backend suite, ruff and mypy.
- [ ] **Step 8:** Commit `feat(logs): store miss events and per-bucket min/max hit`.

### Task 2: Stored tickers

**Files:** Modify `app/esi/client.py`, `app/ingest/pipeline.py`. Create `app/ingest/tickers.py`. Test `tests/test_tickers.py`.

**Interfaces — Produces:**
- `EsiClient.fetch_tickers(corp_ids: list[int], alliance_ids: list[int]) -> tuple[dict[int, str], dict[int, str]]` — best-effort; failures are omitted.
- `async fill_missing_tickers(session, esi, corp_ids: set[int] | None = None, alliance_ids: set[int] | None = None) -> int` — fetches tickers for rows where `ticker IS NULL` (restricted to the given ids when passed), writes them, returns the number filled.
- CLI `python -m app.ingest.tickers` backfills every row lacking a ticker; applies migrations first, like the other maintenance CLIs.

- [ ] **Step 1:** Failing tests with a fake ESI transport: tickers stored for corp and alliance; a 404 for one entity leaves it null and does not fail the others; rows that already have a ticker are not refetched.
- [ ] **Step 2:** Implement; call `fill_missing_tickers` in the ingest pipeline after killmails are persisted, inside the existing best-effort upstream handling so a ticker failure never fails an ingest.
- [ ] **Step 3:** Run suite, ruff, mypy. Commit `feat(ingest): store corporation and alliance tickers`.

### Task 3: `/pilot-timeline`

**Files:** Create `app/analytics/pilot_timeline.py`. Modify `app/api/fleet.py`, `app/api/schemas.py`. Test `tests/test_pilot_timeline.py`.

**Interfaces — Produces:**

```python
@dataclass
class PilotSeries:
    effect_type: str      # damage|rep_armor|rep_shield|neut|nos|cap_transfer|scram|disrupt|jam|miss
    direction: str        # out|in
    idx: list[int]        # indexes into x, ascending
    sum: list[float]      # abs amount, or count for scram/disrupt/jam/miss
    count: list[int]
    min: list[float | None]
    max: list[float | None]

@dataclass
class PilotTimelineRow:
    character_id: int
    character_name: str
    ship_type_id: int | None
    ship_name: str | None
    side_kind: str
    series: list[PilotSeries]

@dataclass
class PilotTimeline:
    x: list[int]
    bucket_seconds: int
    pilots: list[PilotTimelineRow]

async def pilot_timeline(session, br_id, our_alliance_ids, our_corp_ids, settings) -> PilotTimeline
```

- `x` is identical to `fleet_timeline(...).x` for the same BR (same bucket filter plus `miss`-only buckets excluded from `x`; a miss in a bucket with no other activity is dropped).
- Scram and disrupt series come from `LogEvent` rows with `character_id == pilot` and `authoritative IS TRUE`, not from buckets, because bucket counts include every observer's copy.
- API: `GET /api/brs/{br_id}/pilot-timeline` → `PilotTimelineOut` with `scope: "all" | "own"` and `is_self` per pilot; cached under `("pilot-timeline", br_id)`; non-elevated viewers get `pilots` filtered by `viewer.can_see`.

- [ ] **Step 1:** Failing tests: shape and values for a two-pilot fixture; `x` equals the fleet timeline's; tackle counted once per authoritative event; `test_pilot_timeline_member_sees_only_own`; unknown BR → 404.
- [ ] **Step 2:** Implement analytics, schema and endpoint.
- [ ] **Step 3:** Run suite, ruff, mypy. Commit `feat(api): per-pilot bucket timeline`.

### Task 4: `/entities`, snapshot hit stats, composition additions

**Files:** Create `app/analytics/entities.py`. Modify `app/analytics/fleet.py`, `app/analytics/composition.py`, `app/api/fleet.py`, `app/api/schemas.py`. Test `tests/test_entities.py`, extend `tests/test_composition.py`, `tests/test_e3_fleet_timeline.py`.

**Interfaces — Produces:**
- `GET /api/brs/{br_id}/entities` → `{characters: [{character_id, name, corporation_id, alliance_id}], corporations: [{corporation_id, name, ticker, alliance_id}], alliances: [{alliance_id, name, ticker}], by_name: [{name, corp_ticker, alliance_ticker}]}`. Characters: everyone on the BR's killmails plus characters with buckets in its fights. `by_name`: for `other_name` values in the BR's log events not matching a listed character name, the most frequent non-null ticker pair.
- `ContributionOut` gains `hits: int`, `min_hit: float | None`, `max_hit: float | None`, `quality_counts: dict[str, int]` (damage rows; zero/None/empty otherwise).
- `CompositionPilotOut` gains `corporation_id: int | None`, `alliance_id: int | None`. `CompositionSideOut` gains `losses: int`, `isk_lost: float`.

- [ ] **Step 1:** Failing tests for each addition, including the log-ticker fallback picking the most frequent pair and a member being able to read `/entities`.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run suite, ruff, mypy. Commit `feat(api): entity directory, snapshot hit stats, composition affiliations`.

### Task 5: Frontend API layer

**Files:** Modify `frontend/src/api.ts`, `frontend/src/cache.ts`.

**Interfaces — Produces** (TypeScript mirrors of Tasks 3 and 4): `PilotSeries`, `PilotTimelineRow`, `PilotTimeline`, `BrEntities`, `EntityCharacter`, `EntityCorporation`, `EntityAlliance`, `EntityByName`; `api.pilotTimeline(brId)`, `api.entities(brId)`; `loadPilotTimeline(brId, force?)`, `loadEntities(brId, force?)`; extended `Contribution`, `CompositionPilot`, `CompositionSide`.

- [ ] **Step 1:** Add types, calls and loaders. `npm run build` passes. Committed with Task 6.

### Task 6: Entity directory and `PilotName`

**Files:** Create `frontend/src/entities.tsx`, `frontend/src/components/PilotName.tsx`, tests `entities.test.tsx`, `PilotName.test.tsx`.

**Interfaces — Produces:**

```ts
export interface PilotTags { corpTicker: string | null; allianceTicker: string | null; corpName: string | null; allianceName: string | null; corporationId: number | null; allianceId: number | null }
export interface EntityIndex { byId(id: number | null | undefined): PilotTags | null; byName(name: string | null | undefined): PilotTags | null }
export function buildEntityIndex(e: BrEntities | null): EntityIndex
export const EntityContext: React.Context<EntityIndex>
export function pilotLabel(name: string, tags: PilotTags | null): string   // escaped HTML
export function PilotName(props: { name: string; characterId?: number | null; className?: string }): JSX.Element
```

Rendering: `Name [CORP] <ALLI>`; tickers in a dimmed span; `title` holds full corp and alliance names; unknown tickers omitted.

- [ ] **Step 1:** Failing tests: lookup by id then by case-insensitive name; missing tickers omitted; `pilotLabel escapes name and tickers` (`<img onerror>` in a name, `<` in a ticker).
- [ ] **Step 2:** Implement. `npm test` passes. Commit `feat(web): entity directory and PilotName with tickers`.

### Task 7: `pilotStats.ts`

**Files:** Create `frontend/src/pilotStats.ts`, `pilotStats.test.ts`.

**Interfaces — Produces:**

```ts
export type StatFamily = 'damage' | 'reps' | 'cap' | 'ewar'
export type StatDirection = 'out' | 'in' | 'both'
export const FAMILY_EFFECTS: Record<StatFamily, string[]>
export interface PilotStatRow { characterId: number; total: number; peakPerSec: number; avgPerSec: number | null; min: number | null; max: number | null; hits: number; misses: number | null }
export function computeRows(tl: PilotTimeline, opts: { effects: string[]; direction: 'out' | 'in'; from: number; to: number; family: StatFamily }): PilotStatRow[]
export function totalsRow(rows: PilotStatRow[], rangeSeconds: number, family: StatFamily): PilotStatRow
export function sumSeries(tl: PilotTimeline, characterIds: Set<number>): FleetSeriesItem[]   // dense arrays aligned to tl.x
```

Rules: a bucket is in range when `from <= x[i] < to`; `peakPerSec` = max over buckets of the summed visible effects ÷ `bucket_seconds`; `avgPerSec` = total ÷ `(to - from)`, `null` when the range length is ≤ 0 or family is `ewar`; `min`/`max` ignore nulls and are `null` when none; `misses` is the `miss` series count for the direction when family is `damage`, else `null`; for `ewar`, `total` and `hits` are counts and `min`/`max` are `null`.

- [ ] **Step 1:** Failing tests for each rule, plus: empty range, zero-length range, range clipping a partial bucket, `null min/max renders as blank` (returns `null`, never 0), two pilots summed by `sumSeries`, an unknown character id ignored.
- [ ] **Step 2:** Implement. Commit `feat(web): per-pilot range statistics`.

### Task 8: Timeline chart

**Files:** Create `frontend/src/components/chartPlugins.ts`, `frontend/src/components/TimelineChart.tsx`. Modify `frontend/src/fleet.ts`, `frontend/src/hoverSummary.ts`. Tests `fleet.test.ts` (extend), `TimelineChart.test.tsx`.

**Interfaces — Produces:**
- `toFleetView` returns four panels with ids `damage | reps | cap | ewar` (`PanelId` widened); the hover summary maps `reps` to the rep leaders it showed under `damage`.
- `TimelineChart` props: `{ fleet: FleetTimeline; family: StatFamily; direction: StatDirection; isolatedSeries: FleetSeriesItem[] | null; isolatedIds: Set<number>; selectedRange; onSelectRange; broadcasts; flaggedDeaths; hiddenSeries: Set<string>; onToggleSeries(key: string): void; height?: number }`. It renders the series chips, smoothing, kill-marker, broadcast and reset-zoom controls and one chart.
- `killMarkersPlugin(kills, flaggedDeaths, dimUnless?: Set<number>, label?: (k: KillEvent) => string)`.

- [ ] **Step 1:** Move the plugins verbatim into `chartPlugins.ts`; add the dimming argument.
- [ ] **Step 2:** Tests: four panels from `toFleetView`; chart renders the empty state with no data; direction `out` hides `:in` series chips; isolated series replace fleet series.
- [ ] **Step 3:** Implement. Commit `feat(web): single switchable timeline chart`.

### Task 9: Stats table and breakdown

**Files:** Create `frontend/src/components/StatsTable.tsx`, `frontend/src/components/PilotBreakdown.tsx`, tests.

**Interfaces — Produces:**
- `StatsTable` props: `{ brId: string; rows: PilotStatRow[]; totals: PilotStatRow; pilots: PilotTimelineRow[]; family: StatFamily; direction: StatDirection; selected: Set<number>; onToggle(id: number): void; onClear(): void; range: { from: number; to: number }; scope: 'all' | 'own' }`.
- `PilotBreakdown` props: `{ brId: string; characterId: number; from: number; to: number; family: StatFamily; direction: 'out' | 'in' }`; fetches `api.characterSnapshot`, debounced 150 ms.

- [ ] **Step 1:** Tests: default sort total descending; clicking a header sorts and toggles direction; ticking a row calls `onToggle`; idle rows hidden until "show idle pilots"; blank cells for `null`; EWAR hides Avg/Min/Max/Misses values; "Cycles" header for reps and cap; scope note for `own`; expanding a row requests the snapshot and lists rows for the family and direction.
- [ ] **Step 2:** Implement. Commit `feat(web): linked per-pilot stats table`.

### Task 10: Timeline tab

**Files:** Create `frontend/src/views/br/TimelineTab.tsx`, test.

**Interfaces — Consumes** Tasks 5–9. **Produces** `TimelineTab` props `{ brId: string; reloadKey: number; flaggedDeaths: Map<number, string>; broadcastKey: number }`. URL query `stat`, `dir`, `pilots` read on mount and written with `replace`.

- [ ] **Step 1:** Tests: reads `?stat=reps&dir=in&pilots=1`; switching stat type updates the table header and URL; ticking a row isolates and shows "1 pilot isolated"; clear resets; a stale pilot id is dropped; pilot-timeline failure shows the table error and keeps the chart; no logs shows the upload prompt.
- [ ] **Step 2:** Implement. Commit `feat(web): timeline tab with isolation`.

### Task 11: Involved tab

**Files:** Create `frontend/src/involved.ts`, `frontend/src/components/InvolvedSide.tsx`, `frontend/src/views/br/InvolvedTab.tsx`, tests.

**Interfaces — Produces:**

```ts
export interface CorpGroup { corporationId: number | null; name: string; ticker: string | null; pilots: CompositionPilot[]; losses: number }
export interface AllianceGroup { allianceId: number | null; name: string; ticker: string | null; corps: CorpGroup[]; pilotCount: number; losses: number }
export function groupSide(side: CompositionSide, entities: BrEntities | null): AllianceGroup[]
```

Groups sort by pilot count descending; pilots by damage done descending; corps with no alliance form one group with `allianceId: null` named "No alliance", shown last.

- [ ] **Step 1:** Tests: grouping and ordering; loss counts; pilot with unknown corp lands in "Unknown corporation"; modules toggle; ship grouping mode shows hull counts; by-user mode only when `by_user_available`; clicking a pilot with visible logs navigates to `/brs/:id/timeline?pilots=<id>`; coverage strip text.
- [ ] **Step 2:** Implement, porting pilot-row behaviour (ship picker, side setter, log download, badges) from `FleetsPanel.tsx`. Commit `feat(web): involved tab grouped by alliance and corp`.

### Task 12: Shell, routes, remaining tabs, ticker rollout, removal

**Files:** Create `frontend/src/components/BrHeader.tsx`, `SourcesPanel.tsx`, `views/br/CommsTab.tsx`, `AarTab.tsx`, `ManageTab.tsx`. Rewrite `views/BrDetailPage.tsx`. Modify `App.tsx`, `styles/app.css`, `BroadcastMetrics.tsx`, `PerformancePanel.tsx`, `CoverageMatrix.tsx`. Delete the four superseded files and their tests.

- [ ] **Step 1:** Tests: default route renders Involved; `/brs/1/timeline` renders Timeline; unknown tab falls back; Manage tab hidden for members and its URL falls back to Involved; `/brs/1/characters/5` redirects to `/brs/1/timeline?pilots=5`; header shows result, efficiency and pilot counts.
- [ ] **Step 2:** Read `~/.claude/skills/impeccable/reference/craft-floor.md`, then implement the shell and CSS.
- [ ] **Step 3:** Replace plain pilot names with `PilotName` in broadcasts, performance and coverage; use `pilotLabel` in the kill tooltip and hover summary.
- [ ] **Step 4:** Delete superseded files; fix imports; `npm test` and `npm run build` pass.
- [ ] **Step 5:** Commit `feat(web): tabbed battle report dashboard`.

### Task 13: Verify in the running app and document

- [ ] **Step 1:** On a copy of the dev DB: run the app (migration 5), `python -m app.logs.reparse`, `python -m app.ingest.tickers`.
- [ ] **Step 2:** Open the largest BR as FC and as a member (impersonation). Check each tab, stat-type switch, range drag, isolation, row expansion, pilot click from Involved, tickers. One batched round of fixes, one confirmation round.
- [ ] **Step 3:** Run `~/.claude/skills/impeccable/scripts/impeccable detect --json` on the changed frontend files once; fix what it finds.
- [ ] **Step 4:** Run every check in Global Constraints.
- [ ] **Step 5:** Update `README.md` and the deploy section of `TODO.md` (reparse, ticker backfill). Commit `docs: dashboard overhaul deploy steps`.
