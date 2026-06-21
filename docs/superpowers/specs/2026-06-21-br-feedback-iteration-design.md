# BR feedback iteration — date/time, graph UX, tackle fix, killmail augmentation

Date: 2026-06-21
Status: approved (design), pending implementation plan
Scope: `app/` (gamelog parser, EWAR analytics, killmail parse/persist, fleet-timeline,
composition, SDE weapon roles) and `frontend/` (BR list + detail header, fleet graph
controls, snapshot panel, composition panel)

## Goal

Address post-deployment feedback on the battle-report UI and data model:

1. Show the battle date/time in the BR detail header and on the BR list.
2. Make graph zoom resettable (discoverable button), let the snapshot region be cleared,
   and let the snapshot window be set both by visual shift-drag and by typed UTC timestamps
   (the two stay synchronised).
3. On graph hover, summarise what is happening at that moment — side totals plus the
   pilots **receiving** the most damage and the most reps (the targets of interest).
4. Fix the "applied tackle" false positives, where bystanders appear to tackle each other.
5. Extract more from killmails alone (and enrich with logs where present): per-attacker
   damage, final blow, effective tank, weapon roles, and item losses.

Delivered as one combined plan, implemented in this order so risk/value front-loads:
**§4 (tackle bug) → §1–§2 (quick UI) → §3 (hover) → §5 (killmail).**

## Decisions (from brainstorming)

- **Tackle:** re-attribute third-party EWAR lines to the *real* tackler→target and dedupe the
  same tackle across logs, rather than dropping third-party data or merely hiding it.
- **Hover:** centre on the top damage **receiver** and top rep **receiver** (named); show top
  dealer/repper as secondary. Per-pilot leaders precomputed server-side, aligned to the timeline.
- **Snapshot window:** typed start/end inputs and the shift-drag band are two views of one range
  state — editing either updates the other.
- **Killmail augmentation:** full scope — capture victim `damage_taken`, surface per-attacker
  damage + final blow, a battle-level damage leaderboard, weapon roles, and item-loss breakdown.
- **Composition:** keep the ship-centric view as today and add, per ship/pilot, the
  weapons/effects applied as identified from killmails (enriched by logs where present).

## Current state (verified against the codebase)

- **Date/time:** `BrSummary`/`BrDetail` already carry `battle_at` (actual battle start, nullable)
  and `created_at` (always present), through `_br_to_summary` (`app/api/brs.py:674-675`) to
  `frontend/src/api.ts:40-41`. `format.ts` has `fmtDate`/`fmtTime`/`fmtDateTime`/`isoToEpoch`.
  The list (`BrTimelineTable.tsx:79`) shows time only; the detail header shows neither.
- **Graph:** uPlot (canvas) in `frontend/src/components/FleetGraph.tsx`. Three synced panels via
  `PanelChart`. Plain drag = zoom-in, double-click = zoom-out (native, undiscoverable). Shift-drag
  paints the snapshot band (`rangePlugin`). Zoom bounds tracked in `zoomRef`; full extent
  (`fullMin`/`fullMax`) is computed *inside* `PanelChart` and not exposed to the parent.
- **Snapshot range:** owned by `BrDetailPage.tsx:442` (`range`/`setRange`), passed as
  `selectedRange`/`onSelectRange` to `FleetGraph` and `SnapshotPanel`. No clear control exists.
- **Tackle bug:** `app/logs/parse.py` `_match_ewar()` handles three cases for
  `Warp (disruption|scramble) attempt from <src> to <tgt>`. Case 3 (neither party is "you") is a
  third-party observation; it is recorded against the **log owner** (`character_id = file owner`,
  `direction="in"`) with no authoritative flag. `app/analytics/ewar.py` `fight_ewar()` then groups
  by `character_id` with no filter, and `app/logs/associate.py` rebuilds buckets without dedupe —
  so the same tackle from many logs is attributed to every observer.
- **Killmail data:** `KillmailAttacker.damage_done` and `.final_blow` are parsed
  (`app/killmail/parse.py`), stored (`app/db/models.py`), and persisted
  (`app/ingest/persist.py:258-274`) — but **never surfaced** in any `schemas.py` output.
  `weapon_type_id` is stored but unused. Victim `damage_taken` is **not parsed or stored**.
  `KillmailItem` rows exist but are never aggregated. `composition.py` uses only ship types;
  `reconcile.py` already compares stored `damage_done` against log damage when logs are present.

## Design

### §1 Battle date/time (frontend only)

- **Detail header** — add a "Battle (UTC)" stat to the `BrDetailPage` summary section rendering
  `fmtDateTime(br.battle_at ?? br.created_at)`.
- **BR list** — change `BrTimelineTable.tsx:79` from `fmtTime(...)` to
  `fmtDateTime(br.battle_at ?? br.created_at)` so each row shows date + time. Month grouping stays.

No backend change.

### §2 Graph controls (mostly frontend)

- **Reset zoom** — lift `fullMin`/`fullMax` from `PanelChart` into `FleetGraphCore`. Each panel
  registers a reset callback into a shared ref (mirror the existing `positionersRef` pattern). A
  new **Reset zoom** button by the smoothing controls resets all three synced panels to full
  extent and clears `zoomRef`.
- **Clear snapshot** — add a **Clear** button to the `SnapshotPanel` header (visible when
  `range != null`) wired through a new `onClearRange` prop to `setRange(null)` in `BrDetailPage`.
  Existing `range == null` handling already hides the band and snapshot data.
- **Snapshot window: typed + visual, synchronised** — add two UTC `datetime-local` inputs above
  the graph, bounded to the battle window (`t_start`/`t_end`) and prefilled from the current
  `range`. They edit the **same** `range` state as the shift-drag band:
  - shift-drag → `onSelectRange` updates `range` → inputs re-render to the new values;
  - typing a valid pair (`from < to`, in bounds) → `onSelectRange` updates `range` → the band moves.
  No new state; both paths converge on `range`/`onSelectRange`. Invalid/empty input is ignored
  (no range change). `isoToEpoch` handles the naive-UTC conversion.

### §3 Hover summary (frontend + small backend)

- **Backend** — extend the fleet-timeline response with a `leaders[]` array **aligned index-for-
  index to `x`**. Each entry: `{ top_dmg_taken, top_rep_recv, top_dmg_dealt, top_rep_done }`, each
  a `{ name, ship, amount } | null`. Computed from `LogEventBucket` (keyed by
  `character_id`/`effect_type`/`direction`): per bucket, the character with max incoming damage
  (`damage`,`in`), max incoming reps (`rep_*`,`in`), max outgoing damage, max outgoing reps. Names
  resolve via the existing entity/roster lookup. Absent logs → `leaders` empty/omitted.
- **Frontend** — a new uPlot cursor plugin renders a DOM-overlay tooltip (styled like the existing
  kill-marker tip). At the hovered bucket it shows side totals (DPS out, damage taken, reps) and,
  prominently, **top damage receiver** and **top rep receiver** with name + amount; top
  dealer/repper shown smaller. Reads `leaders[idx]`; no per-mousemove computation. When no log data
  exists it shows a "no log data" line.

### §4 Tackle re-attribution + dedupe (backend)

Fix the false positives by attributing tackle to the real parties and collapsing duplicates.

1. **Parse** (`parse.py _match_ewar`) — for every EWAR line extract both `source` (tackler) and
   `target`, and set `authoritative = (source is "you") or (target is "you")`. Case 3 keeps the
   named `source`→`target` instead of folding into the observer.
2. **Resolve** both `source` and `target` names to entities using the existing name→entity
   resolution (`app/logs/entity.py` / `associate.py`).
3. **Persist** — add an `authoritative` flag to the EWAR representation, and ensure both the
   source entity and target entity are recorded (not just "the other party" relative to the owner).
   For third-party lines, attribute to the real tackler/target — never the log owner.
4. **Dedupe** — collapse tackle relationships by `(fight_id, bucket_ts, source, target,
   effect_type)`, preferring `authoritative = true`, so one tackle seen from N logs becomes one row.
5. **Aggregate** (`ewar.py`) — count "applied" against the source entity and "received" against
   the target entity from the deduped set, replacing the current owner-grouped query.

Schema change + a full **log re-parse** (existing `app/logs/reparse.py`); no re-upload. Friendly-on-
friendly noise disappears because tackle is bound to the actual tackler→target, deduped, never the
bystander. The exact storage shape (new columns on `LogEvent` vs. a dedicated EWAR-relationship
table) is an implementation-plan decision; the dedupe key and attribution rules above are fixed.

### §5 Killmail augmentation — full (backend + frontend)

All items work from killmails alone and enrich when logs exist.

1. **Effective tank** — add `damage_taken` to `ParsedVictim` (`killmail/parse.py`), a
   `Killmail.damage_taken` column (`db/models.py`), and persist it (`ingest/persist.py`). Surface
   as "absorbed N damage before dying" on each loss.
2. **Per-attacker damage + final blow** — expose the already-stored `damage_done`/`final_blow` in a
   new per-loss **damage-attribution** API + panel: attackers ranked by damage with share %, final
   blow highlighted.
3. **Damage leaderboard** — battle-level ranking summing `KillmailAttacker.damage_done` per
   attacker across all kills in the BR (the killmail-only DPS proxy). Where logs exist, cross-check
   against `reconcile.py` output (logs also capture damage to ships that did not die).
4. **Weapon roles in composition** — map `weapon_type_id` → role/range band
   (turret / missile / drone / smartbomb / ewar / tackle) via the SDE. The **composition panel
   keeps its ship-centric layout** and adds, per ship/pilot, the identified weapons/effects applied
   from killmails; logs enrich with actually-applied effects (tackle/neut/jam) where present.
5. **Item-loss breakdown** — aggregate `KillmailItem` per loss by slot (high/med/low/rig/drone/
   cargo), destroyed vs dropped, with value — "what was fit & lost."
6. **Augmentation seam** — killmail-attributed damage is the default; log-applied DPS overlays it
   where logs exist. This reuses the existing `reconcile.py` seam rather than inventing a new one.

## Data flow

- **Date/time:** unchanged backend → existing `battle_at`/`created_at` → new header/list rendering.
- **Hover:** `LogEventBucket` → `leaders[]` aligned to `x` in fleet-timeline → tooltip reads by index.
- **Tackle:** gamelog line → parse (source/target/authoritative) → entity resolve → persist →
  dedupe by `(fight_id, bucket_ts, source, target, effect_type)` → `ewar.py` source/target counts.
- **Killmail:** ESI/zKB killmail → parse (now incl. `damage_taken`) → persist (new column) →
  analytics (attribution, leaderboard, weapon roles, item losses) → schemas → panels.

## Testing

- **Date/time:** component test asserting `fmtDateTime` output appears in list row and detail header;
  null `battle_at` falls back to `created_at`.
- **Graph controls:** unit tests that Reset-zoom restores full extent + clears `zoomRef`; Clear
  sets range null; typed input ↔ band stay in sync (typing updates range; range updates inputs);
  out-of-bounds/invalid input is rejected.
- **Hover:** backend test that `leaders[]` length equals `x` length and picks the correct max
  receiver/dealer per bucket on a fixture; absent logs → empty. Frontend test that the tooltip
  renders the named top receivers from a fixture.
- **Tackle:** parser tests for all three EWAR cases incl. `authoritative`; an aggregation test with
  multi-log fixtures (own + bystander) proving one deduped relationship and **no** bystander
  attribution / no friendly-on-friendly.
- **Killmail:** parse/persist test for `damage_taken`; attribution ranking + share %; leaderboard
  summation; weapon-role mapping for representative type ids; item-loss slot aggregation. Verify on
  a real BR that composition still renders ships and now shows weapons/effects.

## Out of scope / YAGNI

- No change to side identification, ISK outcomes, or the ingest/zKB fetch pipeline.
- No reconstruction of spatial positions (that is the separate `eve-battle-reconstruction` project).
- No new EWAR effect types beyond what the parser already recognises.
- Typed inputs drive the snapshot **window**, not graph zoom (zoom keeps native drag + reset button).
