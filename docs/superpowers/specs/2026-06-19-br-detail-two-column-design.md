# BR Detail — two-column redesign, fleet composition, weapon icons

Date: 2026-06-19
Status: approved (design), pending implementation plan
Scope: `frontend/` (BR detail page + fleet view) and `app/` (fleet/composition APIs)

## Goal

Restructure the BR detail page into a two-column layout with the fleet graph as the
dominant left column and a sticky detail rail on the right. Add a fleet-composition
summary with a Composition / Per-character / By-user toggle, classify the weapon used
in each damage log line and show its icon in the moment-detail panel, and surface the
victim pilot's name on kill markers. Standardise all date/time rendering to ISO dates
and 24-hour UTC times.

## Non-goals

- No change to ingestion, killmail parsing, or the sides/override model.
- No new auth model; "By user" reuses the existing FC/HC gate (`me.can_create_br`).
- Engagements list, log-coverage matrix, and sources editor keep their current
  behaviour — they only move position on the page.
- Full-fleet reconstruction beyond killmail participants is out of scope; composition
  is killmail-derived, consistent with the rest of the app.

## Current state

- `BrDetailPage` is a single-column stack: header → summary stats → ingest/sources →
  Engagements (filter + fight list) → Sides → Fleet Graph → Log Coverage.
- `FleetSection` owns the whole fleet view: the three stacked uPlot panels (`fleet-main`)
  **and** a `fleet-side` contributions panel that only renders after a moment is clicked.
  Selected-moment state (`sliderTime`, contributions fetch) lives inside `FleetSection`.
- `module_name` (the fitted weapon, e.g. "250mm Railgun II") is parsed and stored on
  `LogEvent.module_name`, but `fleet_contributions` neither selects nor returns it.
- `KillEvent` carries `victim_character_id` but no name.
- Date/time is rendered inconsistently: some sites already use UTC `toISOString()` slices
  (kill tooltip, contributions header, chart axes), others use locale methods
  (`BrCard`, `FightList`, `FightDetailPage`, `CharacterTimelinePage`, `LogsPage`).

## Design

### A. Page layout (`BrDetailPage`)

- Full-width **header** (back link, editable title, refresh) — unchanged.
- Full-width **summary strip** (result, ISK efficiency / killed / lost, engagements,
  source, battle time) — unchanged content; battle time switches to ISO/UTC.
- A two-column CSS grid below the strip:
  - **`.col-main` (left, dominant, `1fr`):**
    1. **Fleets** panel (new — section B).
    2. **Fleet Graph** panel (the refactored `FleetGraph`, section F).
  - **`.col-side` (right, fixed ~21rem, `position: sticky`):**
    1. **Moment Detail** (`MomentDetailPanel`, section F) — hint/empty state until a
       moment is clicked, then the source→target breakdown with weapon icons.
    2. **Sides** — existing `SidesEditor` wrapped in a collapsed `<details>`.
- Full-width **below the grid:** Engagements (filter + fight list), Log Coverage,
  Sources. Ingest/refresh progress banners stay where they render today.
- Responsive: collapse to a single column under ~60rem (grid → one column; the right
  rail flows under the left).

### B. Fleets summary

A new `Fleets` panel in the left column with a segmented toggle:
**Composition | Per-character | By user**. "By user" is shown only to FC/HC; for other
users it is hidden (not just disabled).

Each mode renders two side panes (Friendly | Hostile; an `unassigned` pane appears only
when non-empty), classified with the **same** `classify_entity` + per-BR overrides used
by the kill markers, so the composition stays consistent with the graph and updates when
sides change (reuses `reloadKey`).

- **Composition** — ship type × count per side, sorted by count desc, each row: ship
  icon (`images.evetech.net/types/{id}/icon`) + `Nx` + ship name. Header shows
  `pilot_count · hull_count`.
- **Per-character** — one row per pilot: ship icon + pilot name + ship name; pilots who
  lost their ship are flagged. Sorted by ship then name.
- **By user (FC/HC only)** — the per-character rows grouped under their owning
  `user_name`; characters with no roster owner fall into an "Unmatched" group.

Data source: live computation (not the precomputed `BrShipCount`) so all three modes
share one query and respect live overrides:

- Pilots = distinct characters appearing as `KillmailAttacker` or `Killmail` victim
  across the BR's fights. Each pilot's ship = their victim ship if they died, else the
  ship recorded on their attacker rows (most frequent if they appear in several).
- Side = `classify_entity(alliance_id, corp_id, baseline, overrides)`.
- `user_name` is attached from the roster `char_to_user` map **only** when the caller is
  FC/HC; otherwise the field is `null` (privacy — never leak char→user to everyone).
- Names resolved via the existing DB→ESI `_resolve_char_names` helper.

New endpoint: `GET /api/brs/{br_id}/composition`

```
CompositionOut {
  by_user_available: bool            # caller is FC/HC AND roster mapping present
  sides: [
    CompositionSideOut {
      side_kind: 'friendly' | 'hostile' | 'unassigned'
      pilot_count: int
      ships: [ { ship_type_id: int, ship_name: str, count: int } ]   # composition
      pilots: [ {
        character_id: int, character_name: str,
        ship_type_id: int | null, ship_name: str,
        lost: bool, user_name: str | null
      } ]
    }
  ]
}
```

The frontend derives Composition from `ships`, Per-character from `pilots`, and By-user
by grouping `pilots` on `user_name`. `user_name` is non-null only when
`by_user_available` is true.

### C. Weapon classification + icons (moment detail)

- **Backend** (`fleet_contributions`): add `LogEvent.module_name` to the select. For
  `damage` rows, classify the module:
  1. Exact match — look up `module_name` in `InventoryType.name`; if found, use that
     `type_id` as the icon and tag `weapon_category` from the type's group where known.
  2. Family fallback — keyword-classify the name into a family and use a representative
     `type_id` for that family:
     - railgun / blaster → hybrid; autocannon / artillery → projectile;
       pulse / beam → laser; missile / rocket / torpedo → missile;
       known drone names → drone; smartbomb → smartbomb; bomb → bomb; else → other.
  Return `module_name`, `icon_type_id` (nullable), and `weapon_category` on each row.
  Non-damage rows return `module_name=null`, `icon_type_id=null` (frontend keeps the
  effect icon).
- Classifier lives in a small pure module (`app/analytics/weapons.py`) with its own unit
  tests; the exact-match step takes a `name → type_id` map the caller builds once per
  request from the `module_name`s in the bucket.
- **Frontend** (`MomentDetailPanel` row icon): if `icon_type_id` is set, render the EVE
  item icon with `module_name` as the tooltip; otherwise fall back to the existing
  `EffectIcon` for the effect type. `ContributionsResponse.rows[]` gains
  `module_name`, `icon_type_id`, `weapon_category`.

### D. Kill marker — victim pilot name

- **Backend**: `KillEvent` / `KillEventOut` gain `victim_character_name`. In
  `fleet_timeline`, resolve victim character ids to names via the same DB→ESI helper used
  for contributions (batch all victim ids in one resolve).
- **Frontend**: `KillEvent` type gains `victim_character_name`; the kill tooltip
  (`showTip`) renders the pilot name on its own line under the ship name.

### E. Date/time standard

- Add to `frontend/src/format.ts`:
  - `fmtDate(x)` → `YYYY-MM-DD` (UTC)
  - `fmtTime(x, withSeconds?)` → `HH:MM` / `HH:MM:SS` (24h UTC)
  - `fmtDateTime(x)` → `YYYY-MM-DD HH:MM` (UTC)
  All built on `toISOString()` slices; accept `Date | number(epoch s) | ISO string`.
- Replace the locale-based calls in `BrCard`, `FightList`, `FightDetailPage`,
  `CharacterTimelinePage`, `LogsPage`, and switch the already-UTC sites (kill tooltip,
  contributions header, chart axes) to the shared helpers for one source of truth.

### F. Component architecture (the `FleetSection` split)

`FleetSection` is split so the page can own the shared selected-moment state (Approach A):

- **`FleetGraph`** — the three stacked panels, controls, kill legend, slider/kill-marker
  plugins. Props gain `onSelect(ts: number | null)`; it no longer renders the
  contributions panel. Internal refs/positioners (slider line, kill markers) stay inside
  it. It calls `onSelect` on click/drag and on close.
- **`MomentDetailPanel`** — given `brId` + selected `ts`, self-fetches
  `api.contributions` (debounced), renders the target cards with weapon/effect icons and
  the empty/hint state. This is the extracted `ContributionsPanel` + `TargetCard` +
  `EffectIcon`, plus the weapon-icon branch from section C.
- **`BrDetailPage`** owns `selectedTs` state, passes a setter to `FleetGraph` and the
  value to `MomentDetailPanel`, and lays out the two columns.

Existing test ids are preserved where they still apply (`fleet-chart-area`,
`fleet-kill-legend`, `fleet-contrib`); `FleetSection.test.tsx` is reworked to target the
split components.

## API summary

- `GET /api/brs/{br_id}/composition` → `CompositionOut` (new).
- `GET /api/brs/{br_id}/contributions` → rows gain `module_name`, `icon_type_id`,
  `weapon_category`.
- `GET /api/brs/{br_id}/fleet-timeline` → `kills[]` gain `victim_character_name`.

## Testing

- **Backend**
  - `app/analytics/weapons.py` classifier: exact match, each family keyword, unknown →
    `other`, and `null` for non-weapon module names.
  - `composition`: side classification with and without overrides; per-character dedup
    (reship / died-vs-attacked ship choice); `user_name` present only for FC/HC and null
    otherwise; pilot_count correctness.
  - `fleet_timeline`: `victim_character_name` resolved and falls back gracefully when a
    name is unknown.
  - `fleet_contributions`: damage row carries `icon_type_id`; non-damage row does not.
- **Frontend**
  - `format.ts`: `fmtDate`/`fmtTime`/`fmtDateTime` for epoch, Date, and ISO inputs at a
    known UTC instant (no locale dependence).
  - `FleetGraph` + `MomentDetailPanel`: loading/empty/error, toggles, kill legend,
    click-to-select drives the detail panel, weapon icon vs effect-icon fallback.
  - `Fleets` panel: three toggle modes render; By-user mode hidden for non-elevated
    users; composition counts and side split correct from a fixture.
  - `BrDetailPage`: two-column structure present; a moment click populates the right rail.

## Risks / open questions

- **Composition cost** — computing pilots live from attackers/victims per request is an
  extra query set. Acceptable at current BR sizes; `BrShipCount` remains available as a
  fast path / cross-check if profiling shows a problem.
- **Module-name → type_id matching** — log module names should match SDE `InventoryType`
  names, but faction/abyssal/edge cases will miss and fall to the family icon by design.
- **Sticky rail height** — if the Sides editor is expanded the rail can exceed viewport
  height; Sides stays collapsed by default and Moment Detail sits above it to stay visible.
