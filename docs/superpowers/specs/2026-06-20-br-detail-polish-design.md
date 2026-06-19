# BR Detail polish — SDE type data, parser fix, snapshot UX

Date: 2026-06-20
Status: approved (design), pending implementation plan
Scope: `app/` (SDE loader, gamelog parser, ingest re-parse, snapshot/composition analytics)
and `frontend/` (fleet graph controls + axis, snapshot panel, fleets panel)

## Goal

Close the data-quality and interaction gaps found while testing the BR detail page: load the
full EVE type catalogue so every weapon and ship name resolves; fix the gamelog parser so
EWAR/cap/rep/neut/nos targets split into `Character (Ship)` instead of a merged string;
separate the snapshot gesture from zoom; render the fleet-graph axis in UTC; rebuild the
snapshot as a full-height, effect-clustered, hover-expanding panel; and make lost-ship rows in
the per-character composition link to zKillboard.

## Decisions (from brainstorming)

- **Type data:** load the full EVE SDE type catalogue into `InventoryType` (the table is
  currently sparse — 506 rows — populated only from killmail ESI resolution).
- **Parser:** the missing names are a parser bug, not missing data. Fix it with an
  SDE-dictionary-driven target splitter and **re-parse the stored gamelog files** (no re-upload).
- **NPCs:** NPC/Sleeper/Drifter targets are a bare type name with no character (`Bhaalgorn`,
  `Arithmos Tyrannos`). Their snapshot title is just that name — `Character (Ship)` applies only
  where the log carries a character.
- **Snapshot gesture:** Shift-drag paints the snapshot band; plain drag = zoom-in and
  double-click = zoom-out stay native (replaces the colliding two-click protocol).
- **Snapshot layout:** full viewport height, no truncation; cluster by effect type
  (Damage · Reps · Cap · EWAR) with a per-cluster summary that expands on hover.

## Current state (verified against real data)

- `InventoryType`: 506 rows; missing common modules (`Rocket Launcher II`, faction charges like
  `Caldari Navy Nova Rocket`), so weapon-icon resolution fails for any name not already pulled
  from a killmail. Only 1/23 damage rows missed in the sample, but the gap is real.
- Gamelog target formats in the real logs:
  - **Damage** (works): `Name[CORP](Ship) - Module - Quality` — ship in parentheses.
  - **Player non-damage** (broken): `ShipType CharacterName [CORP] <ALLI>` — ship and name
    concatenated with no delimiter (`Guardian Jennifer Hibra [NVACA] <NV>`).
  - **NPC** (no character): a bare type name (`Bhaalgorn`, `Arithmos Tyrannos`).
  The parser (`app/logs/parse.py`) hardcodes a NEW (`Name [CORP][ALLI] Ship`) vs OLD
  (`Ship [ALLI][CORP] [Name]`) encoding per effect and guesses wrong for cap/rep/nos/neut and
  incoming EWAR, dumping the whole string into `other_name` with `other_ship_name = NULL`
  (split rate: damage 100%, cap_transfer 0%, rep_armor 0%, nos ~10%, scram-in ~9%).
- Uploaded gamelogs are stored (`GamelogFile.stored_path`), so re-parsing needs no re-upload.
- `FleetGraph` x-axis uses uPlot's default (local-time) tick formatting; the snapshot two-click
  collides with drag-zoom and double-click-reset. The snapshot panel truncates each target card
  to 12 rows ("+N more") and scrolls in a fixed-height box.
- `CompositionPilot` has no killmail reference, so lost ships can't link to zKill.

## Design

### A. SDE type catalogue loader

A loader populates `InventoryType` with the full published EVE type set so name→type_id resolves
for every weapon, charge, ship, and NPC.

- **Source:** Fuzzwork CSV dumps (no auth): `invTypes.csv` (`typeID, groupID, typeName, published`)
  and `invGroups.csv` (`groupID, categoryID`). Downloaded into `SDE_DIR` (config) by a refresh
  script; the loader reads from there.
- **Schema:** add `group_id: int | null` and `category_id: int | null` to `InventoryType`
  (nullable, additive). Load only `published = 1` types.
- **Ship-like set:** types whose `category_id` is Ship (6) or Entity/NPC (11) form the
  "entity name" dictionary used by the parser splitter (section B).
- **Refresh script** (`scripts/refresh_sde.py`): fetch the two CSVs, upsert `InventoryType`.
  Runnable locally and at deploy; idempotent. Type ids are stable, so a stale SDE only misses
  the newest items.
- **Graceful degradation:** if the SDE isn't loaded, behaviour is today's (sparse table +
  weapon family fallback); nothing breaks. Demo mode keeps its fixtures.
- Run the loader once against the dev DB so the live app reflects the fix.

### B. Gamelog parser fix — SDE-dictionary target splitter

Replace the per-effect NEW/OLD encoding guess with one robust, dictionary-driven splitter.

- **Pure helper** `split_entity(text: str, entity_names: frozenset[str]) -> tuple[str | None, str | None]`
  returns `(character_name, ship_name)`:
  1. Strip corp `[...]`, alliance `<...>` and HTML-encoded `&lt;...&gt;` tickers and collapse
     whitespace.
  2. Find the **longest type-name token** in `entity_names` that is a prefix (player format
     `Ship Name`) — that's the ship; the remainder is the character.
  3. If no leading match, try the longest **suffix** match (NEW format `Name … Ship`).
  4. If the whole cleaned string is itself an entity name → NPC: `(None, name)`.
  5. If nothing matches → `(cleaned, None)` (unknown; character only).
- The affected matchers (`_match_ewar`, `_match_neut_out`, `_match_nos`, `_match_rep`,
  `_match_cap`) stop hardcoding an encoding; they extract the raw target substring and defer the
  split to `split_entity`. Because `split_entity` needs the SDE dictionary, the split is applied
  in the **ingest step** (`app/logs/ingest.py`), which loads the ship-like name set from
  `InventoryType` once per file and, **only for events the parser left unsplit**
  (`other_ship_name is None` and not `damage`), sets `other_name` / `other_ship_name` from it.
  `parse.py` stays pure (it carries the raw target through); damage keeps its parse-time split.
- Damage is unchanged (its parenthesised ship already splits correctly).

### C. Re-parse stored gamelogs

A maintenance pass re-derives `LogEvent` rows for already-ingested files with the fixed parser:

- `reparse_gamelogs(session, settings)` iterates `GamelogFile` rows with a `stored_path`,
  re-reads the file, re-parses + re-splits (section B), and replaces that file's `LogEvent`
  rows (delete + re-insert under the same `file_id`), then re-runs fight association for the
  affected BRs so the new events are stamped to fights.
- Exposed as a script (`scripts/reparse_logs.py`) and run once against the dev DB. Idempotent;
  per-file failures log and skip.

### D. Snapshot analytics — NPC titles

`fleet_snapshot` already returns `target_name` + `target_ship`. With B/C applied:
- Player rows: `target_name = character`, `target_ship = ship` → header `Character (Ship)`.
- NPC rows: `target_name = NPC name`, `target_ship = null` → header shows just the NPC name.
No backend shape change beyond what B/C populate; the frontend header logic already renders
`Name (Ship)` when ship is present and `Name` alone otherwise (section H keeps that).

### E. Weapon icons

With the full catalogue (A), the snapshot's existing exact-name `InventoryType` lookup resolves
every logged weapon/charge (e.g. `Caldari Navy Nova Rocket`). The keyword family fallback remains
only for the rare unresolved name. Optionally, weapon family can be derived from `group_id` when
present (more accurate than keyword matching), but the icon itself comes from the exact match.

### F. Fleet-graph axis — UTC

Add a custom uPlot x-axis `values` formatter rendering `YYYY-MM-DD` and `HH:MM:SS` from
`toISOString()` slices (UTC), and confirm the cursor/time-series value formatter and kill-marker
tooltip use the same UTC slices (they already do for time; add the date where a full timestamp is
shown). No locale/`time:true` default formatting.

### G. Snapshot gesture — Shift-drag

Replace the two-click range with **Shift-drag**:
- On a graph, `mousedown` with `shiftKey` starts a band; `mousemove` extends it; `mouseup` sets
  `selectedRange = {from, to}` (ordered). Without Shift, the event falls through to uPlot's
  native drag-zoom; double-click keeps native zoom-out.
- The range band + draggable START/END handles (already built) remain for fine-tuning after a
  Shift-drag. Remove the two-click `handleRangeClick` protocol and the drag-distance click guard
  (no longer needed). Kill-marker ctrl/cmd-click-to-zKill is unchanged.

### H. Snapshot panel — full height, effect clusters, hover-expand

Rebuild `SnapshotPanel` layout:
- The panel fills the available viewport height (its own vertical scroll); the right rail is
  sticky. No per-card row truncation ("+N more" removed) — every source row is present.
- Content clusters by **effect family** derived from `effect_type`: **Damage** (`damage`),
  **Reps** (`rep_armor`, `rep_shield`), **Cap** (`neut`, `nos`, `cap_transfer`), **EWAR**
  (`scram`, `disrupt`, `jam`). Each cluster shows a summary line (label · target count ·
  aggregate magnitude). **Hovering a cluster expands** it to its `Character (Ship)` target groups
  (busiest-first, as today) with source rows, weapon icons, and quality tags. Expanded state on
  hover (with a click-to-pin affordance so it stays open while scrolling is a nice-to-have, not
  required).

### I. Clickable kills in per-character composition

- `fleet_composition` records, per lost hull, the killmail it died on: `CompositionPilot` gains
  `killmail_id: int | null` (set when `lost = true`, from the victim `Killmail.killmail_id` for
  that hull; null otherwise).
- `CompositionPilotOut` + frontend `CompositionPilot` gain `killmail_id`.
- In `FleetsPanel` Per-character and By-user views, a lost row links to
  `https://zkillboard.com/kill/{killmail_id}/` (new tab, `noopener`); the `✗` loss marker is the
  click target.

## API / schema changes

- `InventoryType` gains `group_id`, `category_id` (nullable).
- `CompositionPilotOut` + frontend `CompositionPilot` gain `killmail_id: int | null`.
- No change to the snapshot response shape (B/C change values, not fields).

## Testing

- **Backend**
  - `split_entity`: player `Ship Name [tickers]` → (char, ship) incl. multi-word ship names
    (`Tempest Fleet Issue Bob` → `("Bob", "Tempest Fleet Issue")`); NEW-style suffix; NPC
    bare-name → `(None, name)`; unknown → `(cleaned, None)`; ticker/`&lt;&gt;` stripping.
  - SDE loader: CSV rows upsert into `InventoryType` with `group_id`/`category_id`; published
    filter; idempotent re-run.
  - Ingest split: an EWAR/cap/rep line with the real concatenated format yields split
    `other_name` + `other_ship_name` given a seeded ship-name set.
  - Re-parse: a `GamelogFile` with a stored file is re-parsed; its `LogEvent` rows are replaced
    and re-stamped to fights.
  - `fleet_composition`: lost hull carries `killmail_id`; non-lost hull is null.
- **Frontend**
  - `FleetGraph`: Shift-drag sets a range; plain drag/double-click do not (native zoom path
    untouched); two-click protocol removed.
  - Axis formatter renders UTC `YYYY-MM-DD` / `HH:MM:SS`.
  - `SnapshotPanel`: clusters render with summaries; hovering a cluster expands its target groups;
    no truncation; `Character (Ship)` for players, bare name for NPCs.
  - `FleetsPanel`: a lost per-character row links to the killmail; non-lost rows do not.

## Risks / open questions

- **Fuzzwork availability / size:** `invTypes.csv` is large; load only published types and the
  columns needed. If Fuzzwork is unreachable, the loader logs and leaves the sparse table (graceful
  degradation). The CCP SDE JSONL is an equivalent fallback source.
- **Splitter ambiguity:** a character whose name begins with a ship-type word is rare; longest-
  prefix favours the ship, which is correct for the `Ship Name` format. Restricting the dictionary
  to Ship/Entity categories reduces false matches.
- **Re-parse cost:** bounded by the number of stored gamelog files; run as a one-off / on demand,
  not in the hot path.
