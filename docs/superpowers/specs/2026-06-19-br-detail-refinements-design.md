# BR Detail refinements — snapshot ranges, composition reships, ISK destroyed

Date: 2026-06-19
Status: approved (design), pending implementation plan
Scope: `frontend/` (BR detail page, fleet graph, snapshot panel, fleets panel) and
`app/` (snapshot/contributions analytics, composition analytics, killmail ingest)

## Goal

Refine the redesigned BR detail page and the data behind it: simplify the page layout,
replace the 5-second snapshot with a user-selected time range, enrich the snapshot detail
(target ship type, weapon icons, hit quality, effect-count ordering), make the fleet
composition reship-aware and capsule-free, harden kill-marker interaction, and recover the
ISK-destroyed value that zKillboard already calculates.

## Non-goals

- No change to the gamelog parser, the sides/override model, the auth/elevation model, or
  the by-user privacy gate (composition `user_name` stays FC/HC-only).
- The per-fight detail route (`FightDetailPage`) stays in the codebase; it simply loses its
  entry point from the BR detail page.

## Decisions (from brainstorming)

- **Snapshot selection:** two-click range (click START, click END, third click resets;
  markers draggable). Plain clicks don't trigger the existing drag-to-zoom.
- **Reships:** count every distinct hull a pilot fielded (a reship adds to each hull's
  tally; `pilot_count` stays distinct characters); each hull row carries a reship badge.
- **ISK destroyed:** capture `zkb.totalValue` from the `/related/` response; backfill any
  still-missing value via zKill `/api/killID/{id}/`.
- **Summary panel** keeps the existing stats *and* adds system + ISK destroyed.
- **Removing the fight list** means per-fight pages are unreachable from the UI (accepted).

## Current state (verified)

- `LogEvent` already stores `other_ship_name` and `quality` (both parsed and persisted via
  `app/logs/ingest.py`), but `fleet_contributions` selects neither.
- `fleet_contributions` (`app/analytics/fleet.py`) aggregates by
  `(character_id, target, effect_type, direction)` and sorts groups by total value; the
  endpoint `GET /api/brs/{id}/contributions?at=<epoch>` covers a single `BUCKET_SECONDS`
  window. The frontend `MomentDetailPanel` fetches one `at` and groups by target name.
- `fleet_composition` (`app/analytics/composition.py`) assigns each pilot exactly one ship
  (victim ship if died, else most-frequent attacker ship); capsules are included.
- `Killmail.total_value` is `NULL` for **all** killmails: the `/related/` resolver
  (`app/ingest/sources/zkillboard.py::_extract_refs_from_related`) keeps only `zkb.hash`,
  and killmail bodies are fetched from ESI, which carries no ISK value. `app/killmail/parse.py`
  already reads `zkb.totalValue` when a `zkb` envelope is present.
- Kill markers (`FleetGraph.tsx::killMarkersPlugin`) open zKill on a plain click, colliding
  with the snapshot click. `.page` is `max-width: 72rem`.
- Capsule is `type_id` 670.

## Design

### A. BR detail page layout

`BrDetailPage` is restructured top-to-bottom:

1. Header (back link, editable title, refresh) — unchanged.
2. **Summary** panel (full width, first below the title): the existing stats (result,
   ISK efficiency, ISK killed, ISK lost, source) **plus** the engagement **system(s)** and a
   prominent **ISK destroyed**. The BR detail response gains a `systems: list[str]` field —
   the distinct `SolarSystem.name`s of the BR's fights (resolved backend-side from
   `Fight.system_id`).
3. **Sides** panel (full width, single column): `SidesEditor`, expanded (not collapsed),
   moved out of the right rail.
4. **Two-column area:** left `.br-col-main` = Fleets panel + Fleet graph; right
   `.br-col-side` = the **Snapshot** panel only (Sides no longer here).
5. **Log Coverage** (full width, below) — unchanged.

Removed: the "Engagements" heading, the "Filter sub-engagements" `<details>` + `FilterBuilder`,
and the `FightList` (plus the now-unused fight-filter state/handlers on the page).

Width: `.page` `max-width: 72rem → 80rem`.

### B. Kill markers

In `killMarkersPlugin`, the marker click handler opens zKill **only** when
`ev.ctrlKey || ev.metaKey`; otherwise it does not `stopPropagation`, so the click reaches the
graph's snapshot handler. The hover tooltip gains a dim "⌃-click → zKill" line.

### C. Snapshot panel

- **Rename** `MomentDetailPanel` → `SnapshotPanel`; the heading reads **"Snapshot"**.
- **Range model:** the page owns `selectedRange: { from: number; to: number } | null`
  (epoch seconds) in place of `selectedTs`. `FleetGraph` gains `selectedRange` +
  `onSelectRange`. The slider plugin becomes a **range plugin**: a shaded band between a
  START and END handle. Click logic on the graph: 1st click sets START (END = START), 2nd
  click sets END (ordered so `from ≤ to`), 3rd click starts a new range from that point.
  Each handle is independently draggable to fine-tune; all handle drags `stopPropagation`
  so native drag-zoom is unaffected. Synced across the three panels as the slider is today.
- **Backend:** replace `GET …/contributions?at=` with `GET …/snapshot?from=<epoch>&to=<epoch>`,
  backed by `fleet_snapshot(session, br_id, from_ts, to_ts, settings)` (renamed from
  `fleet_contributions`) which aggregates `LogEvent`s with `from_ts ≤ ts < to_ts`. The
  response container (`ContributionsOut`) carries `from_ts` + `to_ts` (replacing the single
  `at` + `bucket_seconds`) and `rows`; the panel header shows the selected window as
  `HH:MM:SS → HH:MM:SS UTC`.
- **Target headers — `Character Name (Ship Type)`:** select `LogEvent.other_ship_name`,
  carry a cleaned `target_ship` alongside `target_name`, and group by
  `(target_name, target_ship)`. The card header renders `target_name (target_ship)`, or just
  `target_name` when the ship is unknown.
- **Ordering:** sort target groups by **row count desc** (number of distinct
  source→effect rows in the group), tiebreak by total value desc — busy targets on top,
  single-source effects at the bottom. Rows within a group stay value-sorted.
- **Weapon icons:** each damage row resolves its dominant module to a real EVE icon (the
  existing exact-name `InventoryType` match + family fallback). Verification step: run the
  real-data logs through resolution and confirm coverage; load any missing weapon/charge/drone
  `InventoryType` rows from the SDE so *every* logged weapon resolves exactly, leaving the
  family fallback only for genuine misses.
- **Hit quality:** for damage rows, track the count per `quality`
  (`penetrates`/`wrecks`/`smashes`/`glances`/`hits`/`grazes`/…) across the window and surface
  the **dominant** quality label (with its share) as a small tag on the row. `ContributionOut`
  gains `quality: str | null` (the dominant quality for damage rows, else null).

### D. Composition / per-character / by-user — capsules & reships

`fleet_composition` moves from one-ship-per-pilot to a **hull set** per pilot:

- For each character, collect the distinct **non-capsule** `ship_type_id`s they appear in as a
  `Killmail` victim or `KillmailAttacker` (capsule `670` excluded everywhere). A hull is
  flagged `lost` if it appears as that character's victim ship. A character with >1 distinct
  non-capsule hull is a **reship**; every hull row for that character carries `reship = true`.
- A character who appears only in a capsule (podded, ship-loss not in the BR) yields a single
  hull-less pilot row (`ship_type_id = null`, `ship_name = "Unknown"`), counted in
  `pilot_count` but not in ship tallies.
- `CompositionSide.ships`: count of distinct **(character, hull)** pairs grouped by hull
  (a reship contributes to each of its hulls). `pilot_count`: distinct characters.
- `CompositionSide.pilots`: one `CompositionPilot` per (character, hull), each gaining
  `reship: bool` (and keeping `lost`, `user_name`, etc.). Side classification per character is
  unchanged (victim entity first, else attacker), via `classify_entity` + overrides.
- Frontend: `CompositionShipOut`/`CompositionPilotOut` gain `reship`; the Per-character and
  By-user rows render a `↻ reship` badge; capsules never appear.

### E. ISK destroyed (killmail ingest)

- `_extract_refs_from_related` additionally captures `zkb.totalValue` per kill, returning
  refs as `(killmail_id, hash, total_value | None)`. The ingest pipeline merges that value
  into the killmail before persistence (e.g. injecting a `zkb` envelope so the existing
  `parse.py` `zkb.totalValue` read populates `Killmail.total_value`).
- **Backfill:** after persistence, any killmail with `total_value IS NULL` is resolved via
  zKill `GET /api/killID/{id}/` (polite — bounded concurrency + spacing, a `User-Agent`
  header, gzip), reading `zkb.totalValue`. Failures log and leave the value null (never block
  ingest). A BR refresh re-runs this, backfilling existing BRs.
- With `total_value` populated, the BR-level ISK rollups (`our_isk_destroyed`,
  `isk_efficiency`) and the kill tooltip ISK become real, and the Summary panel's
  "ISK destroyed" is meaningful.

## API changes

- `GET /api/brs/{id}/snapshot?from=&to=` (replaces `…/contributions?at=`) → `ContributionsOut`
  with `from_ts` + `to_ts` (replacing `at` + `bucket_seconds`) and `rows`.
- `ContributionOut` gains `target_ship: str | null` and `quality: str | null`.
- `CompositionShipOut` / `CompositionPilotOut` gain `reship: bool`.
- BR detail response gains `systems: list[str]`.

## Testing

- **Backend**
  - `fleet_snapshot`: aggregates over `[from, to)`; multi-bucket window sums correctly;
    `target_ship` carried and cleaned; groups orderable by row count; dominant `quality`
    computed for damage rows and null for non-damage.
  - `fleet_composition`: capsule (670) excluded from ships and pilots; a reship (one character,
    two non-capsule hulls) produces two pilot rows both flagged `reship` and increments both
    hull tallies while `pilot_count` counts the character once; a capsule-only pilot yields a
    single hull-less row counted in `pilot_count` but not in ships; `lost` set from victim hull.
  - Ingest: `_extract_refs_from_related` captures `totalValue`; the value persists to
    `Killmail.total_value`; the `/killID/` backfill fills a null value and tolerates failure;
    BR detail `systems` resolves distinct system names.
- **Frontend**
  - `FleetGraph`: two-click range sets START then END (ordered), third click resets; handle
    drag adjusts without zooming; kill-marker plain click does not open zKill, ctrl/cmd-click
    does.
  - `SnapshotPanel`: fetches `from/to`; renders `Name (Ship)` headers; groups ordered by row
    count; weapon icon per damage row; dominant quality tag; window shown as 24h UTC.
  - `FleetsPanel`: capsules absent; reship rows show the `↻ reship` badge in Per-character and
    By-user; composition counts reflect each hull.
  - `BrDetailPage`: no Engagements/filter/fight-list; Summary (system + ISK destroyed) is the
    first panel; Sides is a full-width single-column panel; right rail holds only Snapshot.

## Risks / open questions

- **`/related/` totalValue presence** — if the `/related/` summary omits `totalValue`, the
  `/killID/` backfill covers every killmail (more requests, still polite). Verified during
  implementation against the real endpoint.
- **Snapshot range cost** — a wide window aggregates more `LogEvent` rows than a single
  bucket; bounded by a single fight's logs, acceptable. Guard against an inverted/zero range
  (`from == to` → empty, `from > to` → swap).
- **Reship side ambiguity** — a character seen under two entities on different sides keeps the
  current single-classification rule (victim entity first); cross-side reships are not modeled.
