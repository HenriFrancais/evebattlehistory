# Log-identified off-BR participants + FC/HC logs-list sort

Date: 2026-06-23
Status: Implemented (2026-06-23). Revised during brainstorming: dropped inference for
FC/HC allocation in v1; added per-character ship allocation. ESI resolution runs at
log-upload (write) time to honor the no-ESI-on-read constraint.

## Problem

Some fight participants never appear on the source battle report (BR) because they
were neither killed nor on a killmail — typically logi, links, tackle, and other
support. We already stamp their (and others') combat logs to the fight by time
window, so they are identifiable from logs. They should appear in the fleet
composition on the correct side, with a ship.

Concrete example (2026-06-14 / system 31002150 BR, fight 8): `sexy'beast`
(character_id 1193875509) is off-BR but uploaded logs, repped fleetmates, and was
shot/tackled — clearly a participant, currently invisible in composition.

Second, smaller request: the FC/HC-only logs list at the bottom of a BR should be
sorted by user (alphabetical), then by character (alphabetical) within each user.

## Goals

- Add off-BR participants **that resolve to a known character** to the composition.
- Classify side with the **same mechanism as the rest of the BR** (`classify_entity`:
  baseline blues + FC/HC entity overrides). No interaction-inference.
- Surface off-BR participants' alliances/corps in the existing **sides editor** so
  FC/HC can allocate them to friendly/hostile — entity-level, in v1.
- Determine each participant's ship best-effort from logs; let **FC/HC assign a ship
  type per-character** when it's Unknown (or to correct it) — in v1.
- Resolve unknown counterparty names + their corp/alliance via ESI, caching in the
  `Character`/`Alliance`/`Corporation` tables.
- Mark these participants visually as identified-from-logs (not on the killboard).
- Sort the FC/HC coverage logs list by user then character.

## Non-goals (explicit future features)

- A dedicated **per-character side** override. *Side* allocation stays entity-level
  (alliance/corp), reusing the existing override model; a character's corp/alliance
  is its assignable unit (EVE sides are by entity). (*Ship* allocation, by contrast,
  IS per-character — see below.)
- Name-only participants with **no** resolvable character (after ESI).
- Interaction-based side inference — dropped.

## Decisions (from brainstorming)

1. **Who to add:** off-BR participants resolved to a known `character_id` (never
   name-only).
2. **Side rule:** `classify_entity` only (baseline + FC/HC overrides). No inference.
3. **Unassignable side:** show on the **unassigned** side (from-logs marker); FC/HC
   allocate via the sides editor. Nothing hidden.
4. **Name resolution:** exact (case-insensitive) `Character` match **plus** ESI
   lookup; ESI also supplies corp/alliance (affiliation).
5. **Ship:** best-effort from logs; **FC/HC can assign a ship type per-character**
   via an inline picker on the participant's row.
6. **Ship counts:** a from-logs participant counts toward its ship's tally once the
   ship is **known** (auto-detected **or** FC/HC-assigned); still-Unknown ones are
   excluded and shown in an Unknown group.
7. **Display:** show on their side, from-logs marker, **counted in pilot count**.

## Architecture

A new isolated module computes the off-BR participant set (identity + corp/alliance +
best-effort ship + reps), doing all ESI resolution and persistence. Consumers:

- `br_entities` unions in participants' (alliance, corp) entities → sides editor.
- `fleet_composition` folds participants in, classifying side via `classify_entity`,
  resolving ship via the override→detected→Unknown chain, flagging `from_logs=True`.
- A new endpoint persists FC/HC per-character **ship** overrides.

```
app/fights/offbr_participants.py   (new)
    offbr_log_characters(session, settings, br_id) -> list[OffBrChar]
        # identity + corp/alliance + detected ship_type_id|None + reps_out + user_name
        # ESI resolution + persistence; NO side (caller classifies)

app/db/models.py    (new) BrCharShip: (br_id, character_id) -> ship_type_id   [+ set_by, set_at]
app/esi/client.py   (extend) resolve_ids(names), resolve_affiliations(ids)  (+ demo stubs)

app/analytics/sides_config.py   (br_entities: union off-BR entities)
app/analytics/composition.py    (fleet_composition: fold OffBrChar in; from_logs; ship chain)
app/api/sides.py or app/api/fleet.py   (PUT participant ship override; GET ship-type search)
```

`OffBrChar`: `character_id`, `character_name`, `alliance_id|None`,
`corporation_id|None`, `detected_ship_type_id|None`, `reps_out`, `user_name|None`,
`source` (`"log_owner"`|`"counterparty"`).

### Step 1 — Identify candidates (resolved character_ids, off-BR)

On-killmail chars across the BR's fights (`fight_participant_char_ids`) are excluded.

- **Log-uploaders:** `LogEvent.character_id` stamped to the BR's fights, minus
  on-killmail (`br_logged_char_ids` exists). `sexy'beast` is here.
- **Resolved counterparties:** distinct `other_name`/`source_name`/`target_name` in
  the BR's fight logs, resolved to a `character_id`: (1) exact case-insensitive match
  vs `Character.name`; (2) remainder via ESI `resolve_ids` (category `character`).
  Pre-filter empty/all-non-alphanumeric tokens before ESI. Exclude on-killmail and
  already-captured log-uploaders.

For each candidate, ensure corp/alliance is known: from `Character`, else ESI
`resolve_affiliations`, persisting the character and resolving+persisting its
alliance/corp **names** (existing `/universe/names/`) so the sides editor reads
cleanly and later loads are free.

### Step 2 — Detected ship (best-effort)

`detected_ship_type_id` = most common `other_ship_name` recorded for the candidate as
a counterparty across the BR's fight logs (mapped to `InventoryType`). None ⇒ no
detection. Pure log-uploaders who never appear as someone's counterparty have none.

### Step 3 — Stats

- `reps_out`: existing `_reps_applied_by_char` (log-based).
- `damage_done` / `kill_count`: 0 (not on any killmail).
- `has_logs`: true for log-uploaders.

### Step 4 — Side classification (no inference)

`classify_entity(alliance_id, corporation_id, baseline_alliances, baseline_corps,
overrides)` → friendly / hostile / unassigned, identical to on-BR entities.
Unassigned participants render on the unassigned side.

### Step 5 — FC/HC side allocation (sides editor extension)

`br_entities` additionally enumerates the (alliance, corp) pairs of the off-BR
participants, unioned+deduped with killmail pairs. Any entity that exists only
off-BR now appears in the sides editor's Unassigned column; FC/HC move it as today,
writing a `BrSideOverride` that `classify_entity` applies everywhere. No new override
type, endpoint, or editor UI — only the entity list grows.

### Step 6 — FC/HC ship allocation (new, per-character)

New table `BrCharShip` (PK `br_id`+`character_id`) → `ship_type_id`. A new
FC/HC-only endpoint sets/clears it:
`PUT /api/brs/{br_id}/participants/{character_id}/ship {ship_type_id|null}`
(guarded by `can_create_br`). A `GET /api/ship-types?q=` search over `InventoryType`
ship categories backs the inline picker (id, name, for the icon).

**Ship resolution chain** for a from-logs participant:
`BrCharShip override` → `detected_ship_type_id` → Unknown.

### Step 7 — Composition integration

`fleet_composition` calls `offbr_log_characters` and loads `BrCharShip` overrides,
then for each participant not already in `acc`: classify side, resolve ship via the
chain, and add to `acc` with `from_logs=True`. A **known** ship is added as the
participant's hull (so it flows through the existing per-ship counting and By-ship
rows); an **Unknown** ship leaves them hull-less.

`CompositionPilot` / `CompositionPilotOut` gain `from_logs: bool` (default false).

### Counting rules

- **pilot_count** (per side, incl. unassigned): includes from-logs participants.
- **ship-type counts / By-ship tallies:** include from-logs participants **with a
  known ship** (detected or assigned); exclude still-Unknown ones, surfaced as a
  trailing `Unknown — N (from logs)` row per side. (A from-logs pilot keeps its
  marker even when counted.)

## Frontend (`FleetsPanel.tsx`, `styles/app.css`, api client)

- `CompositionPilot` type gains `from_logs?: boolean`.
- `PilotRow`: when `from_logs`, dimmed `📋 from logs` marker; row dimmed.
- **Inline ship picker:** on a from-logs pilot row, FC/HC (`by_user_available` /
  elevated) see a ship picker; an Unknown ship prompts to set one. Selecting calls
  the PUT endpoint and refreshes composition. Backed by `GET /api/ship-types?q=`.
- By-character / By-user list them under their side (and roster user in By-user).
- By-ship: trailing `Unknown — N (from logs)` row per side for still-Unknown ones.
- Sides editor: unchanged (renders whatever `br_entities` returns).

## Part 2 — FC/HC logs list sort

The FC/HC logs list is the **CoverageMatrix** (`CoverageMatrix.tsx`), rendered in the
order from `br_coverage` (`app/logs/coverage.py`, `GET /api/brs/{br_id}/coverage`),
as `coverage.map(user => user.characters.map(...))`.

Sort in `br_coverage`: users by `user_name` (alphabetical, case-insensitive), then
`characters` within each user by `character_name` (alphabetical, case-insensitive);
any "Unmatched"/no-user group last. Ordering only; no schema change. (Add a
`br_coverage`/`CoverageMatrix` test asserting the order.)

## Testing

- **Identification:** off-BR log-uploader (`sexy'beast`-shaped) and a resolved
  counterparty added; on-BR chars not duplicated.
- **Side:** baseline-friendly → friendly; FC/HC-overridden entity → that side; else
  unassigned. Entity present only via off-BR participants appears in `br_entities`
  and, after a `BrSideOverride`, classifies the participant.
- **ESI:** unknown name resolved via stubbed `resolve_ids`+`resolve_affiliations` is
  added and persisted; junk/NPC names dropped; ESI failure degrades to exact-match
  without breaking composition or the sides list.
- **Ship:** detected from `other_ship_name`; `BrCharShip` override wins over
  detection; PUT endpoint sets/clears it and is FC/HC-gated; cleared+undetected ⇒
  Unknown.
- **Counts:** pilot_count includes from-logs; a from-logs participant with a known
  ship increments that ship's tally; Unknown ones only appear in the Unknown group.
- **Part 2:** coverage ordered by user then character; Unmatched last.
- Full suite green; validate the fight-8 backup adds `sexy'beast` (friendly via
  baseline) and a representative hostile counterparty (unassigned until allocated).

## Risks / mitigations

- **ESI latency / availability** on read-paths (`/sides`, `/composition`):
  best-effort, bounded to unresolved names, persisted, degrades to exact-match;
  never breaks the page. (Could move to BR build / ingest later — out of scope.)
- **ESI affiliation is current, not fight-time** — a corp-changer may classify by
  their new entity; FC/HC can override. Noted.
- **Entity-level side allocation moves a whole corp/alliance** — intended/consistent
  with the rest of the BR.
- **Auto-detected ship may be wrong** (e.g. a reship) — FC/HC override corrects it;
  the override always wins.
- **pilot_count / ship tallies shift vs the raw killboard** — intended; the from-logs
  marker keeps it legible.
