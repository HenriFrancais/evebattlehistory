# Battle report dashboard overhaul

**Date:** 2026-10-04
**Status:** Design approved in conversation — implementation follows directly

## Goal

Make the battle report page a wide, tabbed dashboard whose centrepiece is the
timeline. The timeline gets one large switchable chart and a linked per-pilot stats
table; an Involved tab gives the high-level picture of who fought in what; pilots
can be isolated; and every pilot name carries its corporation and alliance tickers.

## Who it is for

FCs and members reviewing a fight afterwards, on a wide desktop monitor, inside the
NV Tools iframe. Design target is 1600px and wider; narrower screens must stay
usable but are not optimised. This is an Operate surface: scanning and comparing
numbers matter more than expression.

## Decisions (settled during brainstorming)

| Decision | Choice |
| --- | --- |
| Page structure | Tabbed dashboard: Involved, Timeline, Comms, AAR, Manage |
| Default tab | Involved |
| Chart model | One large chart with a stat-type switch (Damage, Reps, Cap, EWAR) |
| Stats table row | One row per pilot, with an Outgoing / Incoming / Both switch |
| Isolation | Tick rows in the stats table; chart, kill markers and table narrow to them |
| Involved grouping | Alliance, then corporation, then pilot; a secondary switch groups by ship |
| Computation | Hybrid: per-pilot 5-second buckets load once, the browser computes range stats |
| Viewport | Desktop inside NV Tools |
| Visual identity | Unchanged: existing dark theme and colour tokens |
| Delivery | One spec, one plan, built together |

## Non-goals

- Hostile pilots as rows of the stats table. They appear in a row's expanded
  breakdown as sources and targets.
- A raw per-event log under the table.
- A compare mode with two charts.
- A mobile layout.
- A new visual identity, new fonts or a new palette.
- The open items in `TODO.md` (shared fights, small-group distributions, and so on).

## Page shell

`/brs/:id` renders a compact header and a tab bar; the active tab fills the width.

- **Header strip** (always visible): back link, title (editable for FC/HC), system,
  battle time in UTC, then result badge, ISK efficiency, ISK destroyed, ISK lost,
  pilot counts per side and engagement count. Source link and Discord thread link
  sit at the right. The ingest warning and ingest progress stay directly under the
  header, above the tabs.
- **Tabs** are routes, so links and the back button work:
  `/brs/:id` (Involved), `/brs/:id/timeline`, `/brs/:id/comms`, `/brs/:id/aar`,
  `/brs/:id/manage`. An unknown tab falls back to Involved.
- **Comms** holds Fleet Broadcasts and Performance.
- **AAR** holds the existing AAR panel.
- **Manage** is shown to FC/HC only and holds sources, sides, the full coverage
  matrix, and the refresh and delete actions. A member who opens the URL directly
  gets the Involved tab.
- The 88rem page cap does not apply to this page; content runs to about 120rem.
- The fullscreen graph overlay is removed.
- `/brs/:id/characters/:charId` redirects to
  `/brs/:id/timeline?pilots=<charId>`; `CharacterTimelinePage` is deleted.

Only the active tab is mounted. Data shared by tabs (BR detail, `me`, the entity
directory, broadcast metrics for flagged deaths) is loaded by the shell and passed
down, using the existing prefetch cache.

## Involved tab

Two columns, friendly then hostile, with an unassigned column only when present.

- **Side header:** side name, pilot count, ships lost, ISK lost.
- **Grouping (default):** alliance, then corporation, then pilot. Each group header
  shows name, ticker, pilot count and loss count. Corporations with no alliance sit
  directly under the side. Groups start expanded and collapse on click; groups sort
  by pilot count, pilots by damage done.
- **Pilot row:** ship icon, pilot name with tickers, ship name, damage done with
  kill count, reps out, loss marker linking to zKillboard, and the existing reship,
  from-logs and has-logs indicators. FC/HC keep the ship picker and side setter on
  from-logs pilots and the log download button.
- **Clicking a pilot name** opens the Timeline tab with that pilot isolated. Only
  pilots with logs the viewer may see are links.
- **Modules toggle:** shows each pilot's weapons and modules inline.
- **Group switch:** `Alliance / corp` or `Ship`. Ship mode is today's hull
  composition view (count per hull with top modules).
- **By user** (FC/HC, when the roster is available): replaces the corp level with
  the NV Tools user, as today.
- **Your coverage strip** (all viewers with characters in the fight): one line
  stating how many of the viewer's characters have logs, with an upload link.
  Replaces the "My Characters" section.

## Timeline tab

Top to bottom: control bar, chart, selection bar, stats table.

### Control bar

- **Stat type:** Damage, Reps, Cap, EWAR (segmented control).
- **Direction:** Outgoing, Incoming, Both. Both draws outgoing above the zero line
  and incoming mirrored below, as today.
- **Series chips** for the current stat type (for example armour and shield under
  Reps; neut, nos and transfer under Cap). The table follows the visible series.
- Kill markers, broadcast kinds, smoothing and reset zoom, as today.

Stat type, direction and isolated pilots are kept in the URL query
(`?stat=reps&dir=in&pilots=1,2`) so a view can be linked.

### Chart

One uPlot chart, full width, about 420px tall. Plain drag zooms, Shift-drag selects
a range, double-click resets, as today. With no pilots isolated it draws the fleet
series from `/fleet-timeline`. With pilots isolated it draws the sum of those
pilots' series from the per-pilot data. Kill markers for losses that are not an
isolated pilot's are dimmed, not removed. The hover summary is shown only when
nothing is isolated.

### Selection bar

Shows the active range (`19:42:10 → 19:44:30 UTC, 2m 20s`) with a clear button and
the two UTC time inputs, and the isolation state (`3 pilots isolated`) with a clear
button.

### Stats table

One row per pilot the viewer may see, plus a totals row. The table always has
content: with no range selected it covers the whole fight window.

| Column | Meaning |
| --- | --- |
| Select | Checkbox; ticked pilots are isolated |
| Pilot | Name with corp and alliance tickers |
| Ship | Hull flown |
| Total | Sum over the range for the visible series |
| Peak /s | Highest single 5-second bucket in the range, divided by 5 |
| Avg /s | Total divided by the range length in seconds |
| Min | Smallest single hit or cycle in the range |
| Max | Largest single hit or cycle in the range |
| Hits | Number of hits or cycles in the range |
| Misses | Number of misses in the range (damage only) |

- For EWAR, Total and Hits are application counts, Peak is the busiest bucket, and
  Avg, Min, Max and Misses are blank.
- For Reps and Cap, Misses is blank and the Hits header reads "Cycles".
- With direction Both, the table shows outgoing; a note under the header says so.
- All columns sort; default is Total descending. Rows with no activity in the range
  are hidden behind a "show idle pilots" toggle.
- The totals row sums the visible rows. It can differ slightly from the fleet chart
  because the fleet series dedupes events logged by both ends.
- **Expanding a row** fetches that pilot's breakdown for the range: target (or
  source, for incoming), weapon or module, total, hits, min, max and the hit-quality
  mix. This replaces the snapshot cards.
- Members see only their own characters as rows, with the existing note explaining
  that the fleet-wide breakdown is FC/HC-only. The chart's fleet series is unchanged.

## Pilot names and tickers

Every place a character is named renders `Name [CORP] <ALLI>`, tickers dimmed, with
the full corporation and alliance names as a tooltip. Unknown tickers are omitted.

- React surfaces use one `PilotName` component.
- HTML-string surfaces (chart hover summary, kill tooltip) use one `pilotLabel`
  helper that returns escaped markup.
- Both read from a per-BR entity directory loaded once by the shell.

Surfaces: Involved rows, stats table and its breakdown, kill tooltips, hover
summary leaders, broadcast tables, performance table, coverage matrix.

## Architecture

### Backend

**Schema (one migration)**

- `corporation.ticker`, `alliance.ticker` (nullable strings).
- `log_event_bucket.min_amount`, `log_event_bucket.max_amount` (nullable floats).

**Tickers**

- ESI corporation and alliance lookups already made at ingest also store the
  ticker. Entities that still lack one are fetched when a BR is ingested or
  refreshed.
- A maintenance command backfills tickers for existing rows.
- For a character on no killmail, the directory falls back to the tickers the log
  parser saw next to that name (`log_event.other_corp_ticker`,
  `other_alliance_ticker`), most frequent value winning.

**Misses**

- The parser recognises miss lines and emits them as `effect_type="miss"` with a
  direction, the other party and the module, and no amount.
- Ingest stores them. Buckets are built for them like any effect, so a bucket's
  `event_count` for `miss` is the miss count.
- Every existing reader that aggregates by effect type already restricts itself to
  known effect types; each is checked so `miss` never enters damage totals, the
  fleet timeline, snapshot rows, reconcile or broadcast analytics.

**Buckets**

- The bucket rebuild also writes the smallest and largest positive `amount` per
  bucket.

**Endpoints**

- `GET /api/brs/{br_id}/pilot-timeline` — per-pilot bucket series for the stats
  table and isolation.
  - `x`: bucket start times (epoch seconds), `bucket_seconds`.
  - `pilots[]`: `character_id`, `character_name`, `ship_name`, `ship_type_id`,
    `side_kind`, `is_self`, and `series[]`.
  - `series[]`: `effect_type`, `direction`, and sparse parallel arrays `idx`
    (index into `x`), `sum`, `count`, `min`, `max`. Misses are their own series
    with `effect_type="miss"`.
  - `scope`: `all` for FC/HC, `own` otherwise. Non-elevated viewers get only their
    own characters.
  - The unredacted result is cached per BR in the derived cache; redaction happens
    per request.
- `GET /api/brs/{br_id}/entities` — the entity directory, visible to all viewers.
  - `characters[]`: `character_id`, `name`, `corporation_id`, `alliance_id`.
  - `corporations[]`: `corporation_id`, `name`, `ticker`, `alliance_id`.
  - `alliances[]`: `alliance_id`, `name`, `ticker`.
  - `by_name[]`: `name`, `corp_ticker`, `alliance_ticker` for names known only
    from logs.
  - Covers every character on the BR's killmails plus log-identified participants.
- `GET /api/brs/{br_id}/characters/{character_id}/snapshot` — unchanged access
  rule; each row gains `hits`, `min_hit`, `max_hit` and `quality_counts`.
- `GET /api/brs/{br_id}/composition` — each pilot gains `corporation_id` and
  `alliance_id`, and each side gains `losses` and `isk_lost`.

Existing privacy rules hold: per-pilot log data for other people's characters is
FC/HC-only and enforced in the API, never in the browser.

### Frontend

New and changed units, each with one job:

| Unit | Job |
| --- | --- |
| `views/BrDetailPage.tsx` | Shell: header strip, tab bar, shared data, tab routing |
| `views/br/InvolvedTab.tsx` | Involved layout, group switch, modules toggle, coverage strip |
| `views/br/TimelineTab.tsx` | Owns stat type, direction, range, isolation; wires chart and table |
| `views/br/CommsTab.tsx`, `AarTab.tsx`, `ManageTab.tsx` | Thin wrappers around existing panels |
| `components/BrHeader.tsx` | Header strip with editable title |
| `components/SourcesPanel.tsx` | Moved out of `BrDetailPage.tsx` unchanged |
| `components/TimelineChart.tsx` | The single uPlot chart and its plugins (from `FleetGraph.tsx`) |
| `components/chartPlugins.ts` | uPlot plugins, moved out of `FleetGraph.tsx` |
| `components/StatsTable.tsx` | Sortable, selectable per-pilot table with row expansion |
| `components/PilotBreakdown.tsx` | Expanded-row breakdown from the character snapshot |
| `components/InvolvedSide.tsx` | One side's grouped pilot list |
| `components/PilotName.tsx` | Name with tickers |
| `pilotStats.ts` | Pure functions: range stats and summed series from bucket data |
| `involved.ts` | Pure functions: grouping pilots by alliance and corp |
| `entities.ts` | Directory lookup, `pilotLabel` for HTML strings, React context |

Removed: `FleetGraph.tsx`, `SnapshotPanel.tsx`, `FleetsPanel.tsx`,
`CharacterTimelinePage.tsx` and their tests, once their behaviour lives in the units
above.

### Data flow

1. The shell loads BR detail, `me` and the entity directory.
2. The Timeline tab loads `/fleet-timeline`, `/pilot-timeline` and broadcast data
   once per BR.
3. Stat type, direction, range and isolation are React state in `TimelineTab`.
   `pilotStats.ts` derives table rows and, when pilots are isolated, the chart
   series. No request is made for these interactions.
4. Expanding a row requests the character snapshot for the current range,
   debounced, and cached per `(character, range)` while the tab is open.

## States and errors

- **Loading:** each tab shows its own loading line; the header renders as soon as
  BR detail arrives.
- **No logs for the BR:** the Timeline tab shows the chart's empty state and a
  prompt to upload logs. The Involved tab is unaffected.
- **Member with no characters in the fight:** an empty table with the scope note.
- **Pilot-timeline request fails:** the chart still renders from the fleet series;
  the table shows the error and isolation is disabled.
- **Directory request fails:** names render without tickers; nothing else changes.
- **Isolated pilot not in the data** (stale link): the id is dropped from the
  selection.
- **Range with no activity:** rows hidden, with a line saying nothing was logged in
  the window.

## Testing

- **Backend (pytest):** miss parsing for each log layout; misses excluded from
  every existing aggregate; bucket min and max; pilot-timeline shape and its
  FC/HC versus member redaction; directory contents and the log-ticker fallback;
  ticker storage from ESI; snapshot hit statistics; composition additions.
- **Frontend (vitest):** `pilotStats` (total, peak, average, min, max, hits, misses,
  range clipping, summing isolated pilots); `involved` grouping; `PilotName` and
  `pilotLabel` escaping; tab routing and the member view of Manage; stats table
  sorting, selection and expansion; Involved pilot click navigating with the pilot
  isolated; the old character URL redirecting.
- The uPlot canvas is not unit-tested, as today. The page is checked in a browser
  against the dev database before the work is called done.

## Deployment

In addition to the steps already listed in `TODO.md`:

1. The migration adds the new columns on first boot.
2. Reparse existing logs once so misses and bucket min/max exist for old battles.
3. Run the ticker backfill once.

Until the reparse runs, old battles show blank Min, Max and Misses.

## Amendments after the first preview (2026-10-04)

These replace the corresponding parts above.

- **Involved tab:** no alliance / corporation grouping. Each side is one row per
  pilot, ordered from the largest hull class to the smallest (capitals, battleships,
  battlecruisers, cruisers, destroyers, frigates, pods), the same hulls together.
  Every row shows the pilot's corp and alliance tickers in line and the alliance
  (else corporation) logo. A pilot who lost their ship has the whole row tinted
  red; the ship name links to the killmail. Modules are shown in line as icons
  with the module name as a hover tooltip, behind a Modules toggle that is on by
  default. The mode switch is Pilots / By main / Ships; By main (characters
  grouped under the NV Tools user they belong to) is offered to FC / High Command
  when the roster is available.
- **Performance tab:** the Comms tab is named Performance (`/brs/:id/performance`).
- **Timeline range:** there is no separate Shift-drag range. Dragging on the chart
  zooms it and the stats table always covers exactly what the chart is zoomed to;
  double-click zooms back out to the whole fight. The two UTC time fields set the
  same zoom window. A press that starts on a kill marker starts the drag too.
- **Who sees the stats table:** every viewer sees the per-pilot summary rows for
  ALL logged pilots and can isolate any of them. The expandable breakdown (targets,
  weapons, hit quality) is offered only to FC / High Command, and to a member for
  their own characters; the character snapshot endpoint enforces this (403).
  `/pilot-timeline` therefore returns every pilot to every viewer, and its `scope`
  field states whose breakdown may be opened.
- **A lost pilot's whole row** on the Involved tab opens the killmail on zKillboard.
- **AAR placement:** the after-action report is not a tab. It sits in its own section
  between the summary header and the tab bar, visible on every tab. Tabs are
  Involved, Timeline, Performance, and Manage for FC / High Command.
- **Members on the Involved tab:** every viewer sees each pilot's reps figure and
  has-logs marker. Downloading a gamelog stays FC / High Command only, except for
  the viewer's own characters.
- **Isolation for members:** a member can isolate only their own characters on the
  timeline (checkbox, URL and the Involved-tab name link alike). The summary rows
  for all pilots stay visible to them. FC / High Command can isolate anyone.
