# NV Battle Reports — Redesign Design

**Date:** 2026-06-19
**Status:** Approved (design); pending spec review → implementation plan
**Author:** Henri French + Claude (Opus 4.8)

## Goal

Redesign the NV Battle Reports app so the engagement timeline, BR detail, and
fleet graph clearly communicate what happened in a fight — who was involved,
what each side fielded, the outcome, and the moment-by-moment state of the
fight — while enforcing character→person privacy and fixing UI contrast.

This redesign exists because the first build shipped but under-served users:
the fleet graph plotted mismatched quantities on one Y axis, links were
unreadable on the dark theme, and character→user mappings leaked to any member.
We are research-informed this time (see References) and building in small,
independently-shippable, test-driven phases.

## Non-goals

- No spatial replay (we have no position data; EVE gamelogs don't carry it).
- No change to the ingest sources (zKillboard resolver, gamelog upload) beyond
  what the new bucket dimension requires.
- No new auth system — we reuse the NV Tools proxy identity + existing
  `can_create_br` / `can_view_character` primitives.

---

## Research summary (analogous products)

| Source | Borrowed idea |
|---|---|
| FFLogs / Warcraft Logs / Archon | Combat **timeline graph** with damage-done / damage-taken / healing curves; **click-drag to select a time window** that scopes all detail views; per-series toggle strip above the graph; per-player overlay toggling; **death recap** = the last hits a victim took before dying, with the events listed. |
| Grafana small-multiples | **Shared time cursor** across stacked panels + shared tooltip ("what was happening in all metrics at time X"); cap tooltip rows for legibility. |
| Diverging-chart best practice | Symmetric central baseline, up = positive / down = negative convention; **prefer blue/orange over red/green** for colorblind safety; reinforce meaning with position + labels, not color alone. |
| br.evetools / zKillboard | **Two-column "Team vs Team"** layout: per-side ISK destroyed/lost, efficiency, pilots, alliances/corps, and **ship composition grouped by type with counts**. |

References with URLs at the end of this document.

---

## Workstream 1 — Data model: target-side dimension + side resolver (foundational)

**Problem.** The graph is fed by `LogEventBucket`, keyed by
`(fight_id, character_id, bucket_ts, effect_type, direction)`. Each underlying
`LogEvent` already carries the other party (`other_name`, `other_corp_ticker`,
`other_alliance_ticker`, `other_ship_name`), but the bucket throws that away, so
we cannot tell rep-to-enemy from rep-to-friendly, or damage-onto-friendly from
damage-onto-hostile.

**Change.**
1. Add `other_side` to `LogEventBucket`'s composite primary key. Values:
   `''` (unknown) / `friendly` / `hostile` / `neutral`. Same NULL→`''` rule as
   the existing `effect_type` / `direction` columns (SQLite PK can't be NULL).
2. New module `app/analytics/sides_index.py`: `build_side_index(fight) -> SideIndex`
   that resolves a log event's other-party to a `side_kind`. The index is built
   per fight from, in priority order:
   - **Friendly config**: `our_alliance_ids` / `our_corp_ids` resolved to
     tickers via ESI → those tickers are `friendly`.
   - **Killmail participants**: every attacker/victim on the fight's killmails,
     resolved to character name + corp/alliance, mapped to their
     `FightSide.side_kind`.
   - **Logged friendly pilots**: any character who uploaded logs and is on a
     friendly side.
   Resolution order per event: character-name (lowercased) → alliance ticker →
   corp ticker → `unknown`.
3. `app/logs/associate.py::_rebuild_buckets_for_pairs` computes `other_side` per
   event when (re)building buckets. Re-association already rebuilds buckets, so
   new ingests backfill automatically; add a one-shot rebuild command/migration
   for existing rows.

**Interfaces.** `SideIndex.classify(name, corp_ticker, alliance_ticker) -> str`.
Pure, unit-testable; the resolver has no DB or network in its hot path (ESI
ticker resolution is done once when building the index, cached).

**Edge cases.** Ambiguous tickers (same ticker both sides — shouldn't happen but
guard) → `unknown`. Parties never on a killmail and never logged → `unknown`.
Neutral third parties → `neutral`.

**Tests.** Resolver classification (friendly/hostile/neutral/unknown, each
resolution tier); bucket rebuild produces the new dimension; NULL→`''` coercion;
re-association idempotency preserved.

---

## Workstream 2 — Fleet graph redesign

### Layout
Stacked small-multiples, sized to fit one screen (compact, collapsible panels),
all sharing one synced time axis. Three panels:

| Panel | Unit | Effects |
|---|---|---|
| Damage & Remote Rep | HP/s | `damage`, `rep_armor`, `rep_shield` |
| Cap warfare | GJ/s | `neut`, `nos`, `cap_transfer` |
| EWAR | events / applications per bucket (count) | `scram`, `disrupt`, `jam` |

> EWAR is counted as applications per bucket (what the logs actually record).
> Rendering it as *active-point duration* (a point held continuously) would
> require modeling cycle duration and is deferred as a possible enhancement.

### Outgoing vs incoming
**Mirrored baseline per panel**: outgoing above zero, incoming below zero. Legend
uses explicit arrows (`out →`, `← in`). This makes "you doing things" vs "things
done to you" unmistakable at a glance, and follows diverging-chart convention.

### Target-side encoding
Each (effect, direction) further splits by `other_side`. Color family conveys
effect; **position conveys direction**; **side** conveys via hue
shade + label; **anomalies** (you can shoot friendlies / rep enemies) get a
hatched warning style so they pop.

Default-visible "expected" series:
- DPS `out → hostile`, DPS `in ← hostile`
- rep `out → friendly`
- neut/nos/cap `out → hostile`, cap `in ← hostile`
- ewar `out → hostile`, ewar `in ← hostile`

Default-hidden anomaly series (one click to reveal, hatched):
- DPS `out → friendly` (friendly fire), DPS `in ← friendly`
- rep `out → hostile` (repping an enemy)

### Smoothing
Centered rolling mean, on by default, with per-effect default windows tuned to
cycle time: fast (`damage`) ≈ 10 s; slow-cycle (`rep`, `neut`, `nos`,
`cap_transfer`, ewar reactivation) ≈ 25–30 s. Global **raw ↔ smoothed** toggle
and a window slider. Smoothing is computed frontend-side from the raw bucket
series so the backend stays a single source of truth.

### Toggling
Hierarchical legend: effect → direction → side, plus presets
**Expected only / Show anomalies / Show all**. Per-character overlay toggling
(respecting access — Workstream 4): members can overlay their own characters;
FC/HC any character.

### Kill markers
Vertical lines spanning all panels, colored by victim side (colorblind-safe
hues), hover shows ship / victim pilot / ISK / side. **Click a kill** → expands
a **kill recap**: the log events applied to the victim in the
seconds before death (default 10 s window, configurable) — incoming
damage/ewar/cap on that victim from logged pilots — reconciling who landed the
kill. Pilot/ship always shown; person-mapping gated.

### "State of the fight" inspection
- **Synced crosshair** across all panels + shared tooltip listing every active
  series value at the hovered bucket (rows capped/grouped for legibility).
- **Drag-to-select a time window** on any panel brushes a segment; the readout,
  kill list, and (if expanded) per-character contributions scope to that segment
  — answering "what was the state of the fight during this stretch?"
- **Pin** a hovered timestamp to freeze the readout and expand per-character
  contributions at that moment (access-gated).

### API
Extend `GET /api/brs/{br_id}/fleet-timeline` to return series keyed by
`(effect_type, direction, other_side)` with the raw (unsmoothed) values, plus
the existing `x`, `kills`, `fights`, `bucket_seconds`. Add a per-character
contributions endpoint for the pin/segment readout, gated by `can_view_character`
/ ownership.

**Tests.** Backend: series include the side dimension; anomaly series present;
contributions endpoint enforces access. Frontend: transform builds mirrored
(in below zero) series; smoothing window math; default-visible vs anomaly
classification; toggle state; brush selection scopes readout.

---

## Workstream 3 — Timeline (BR list) summary cards + inline expand

### Card (collapsed)
Each engagement on the timeline shows at a glance:
- **Groups involved** — per side: top alliances/corps chips + pilot counts.
- **Sides identified** — friendly / hostile / neutral chips.
- **Outcome** — win / tie / loss.
- **ISK killed vs lost** and **efficiency**.
- **My involvement** — count of the viewer's characters involved (on a killmail
  or with logs).
- **Log coverage** — characters-with-logs / in-scope, plus a **"my logs missing"**
  flag when the viewer was involved but hasn't covered all their fights.

### Expand (inline, no navigation)
Two-column **Team vs Team** breakdown (br.evetools convention):
- Per side: ISK destroyed/lost, pilot count, alliances/corps.
- **Ship composition** grouped by ship type with counts (what each side fielded).
- Per-character participation/coverage rows (`on_killmail`, `has_logs`,
  fights covered/missing) — person-mapping gated per Workstream 4.

### API
Extend the `GET /api/brs` list items (or a sibling endpoint) with: per-side
group summary, viewer's involved-character count, and coverage counts +
my-logs-missing flag (computed for the acting user). The expand uses the
existing participants/coverage endpoints (now redacted).

**Tests.** Summary fields computed correctly; my-involvement counts only the
viewer's characters; coverage flag logic; expand renders ship composition;
redaction applied.

---

## Workstream 4 — Access control / privacy (security-sensitive)

**Rule (per user decision):** in-game pilot/character names stay visible to
everyone (they're public on killmails / zKB). Only the character→**NV user**
(real person) mapping is restricted: visible to FC/HC (`can_create_br`) and to a
member for **their own** characters.

**Today's leak:** `GET /api/brs/{id}/participants` and `GET /api/brs/{id}/coverage`
return `user_name` for every character to any authenticated member.

**Change.**
- Central redaction helper `redact_user_name(acting_user, character_id, roster)`
  → returns the `user_name` only when `can_create_br(acting_user)` or the
  character is owned by `acting_user`; otherwise `None`. Pilot name/ship stay.
- Apply at: `/participants`, `/coverage`, the fleet per-character contributions
  endpoint, the kill recap, and anywhere `user_name` is serialized.
- Per-character graphs/overlays continue to enforce `can_view_character`
  (members: own; FC/HC: any).
- Tighten `GET /api/roster/users` (used by the dev impersonation picker) so it
  isn't a person-mapping oracle in production.

**Tests (must-have).** A member cannot recover character→user for a character
they don't own through *any* endpoint (participants, coverage, fleet
contributions, kill recap, roster). A member can see their own mapping. FC/HC
sees all. Regression test per endpoint.

---

## Workstream 5 — Filtering: "involved + missing my logs"

- **Quick toggles** on the timeline: **Involved (me)** and **Missing my logs**.
- Filter engine (`app/analytics/filters.py`) gains user-scoped virtual fields
  computed for the acting user's characters:
  - `my_involved` (bool) — viewer has ≥1 character on a killmail or with logs in
    the BR.
  - `my_logs_missing` (bool) — viewer is involved and ≥1 of their in-scope
    fights lacks their logs.
- The existing advanced FilterBuilder keeps working; these are added fields, not
  a rewrite. Whitelist enforcement (unknown field → `FilterError`) preserved.

**Tests.** Virtual fields resolve against the acting user; quick toggles compose
with existing predicates; whitelist still rejects unknown fields.

---

## Workstream 6 — Color / contrast

- Add a `--link` design token with WCAG-AA contrast on the dark panels (≈
  `#6cb6ff`), a global `a {}` rule, and fix the three bare `<a>` spots that fall
  back to unreadable browser dark-blue: `BrCard.tsx`, `BrDetailPage.tsx` (×2).
- Audit the win/loss/side palette for **colorblind safety** — shift side
  encoding toward blue (friendly) / orange (hostile) where feasible, and ensure
  every color-coded element also carries text/shape (chips already have labels).
- Document the token palette in `app.css` so future colors stay on-system.

**Tests.** A small contrast assertion (token values meet AA against `--panel`);
visual check during verification.

---

## Workstream 7 — log timezone offset (discovered during build)

**Symptom.** On the redesigned fleet graph, kill markers fell outside the log
window. Measuring `dev.db` for the "3v1" BR: killmails/fight = `20:18–21:15`,
but log buckets = `19:26–20:17` — the logs are ~1 hour *behind* the killmails
for the same engagement (the other BR shows the same skew).

**Cause.** `app/logs/parse.py::_parse_ts` stamps every log line `tzinfo=UTC`
with no conversion. EVE writes gamelog line timestamps in the client's **local**
time; killmails (zKB/ESI) are **UTC**. A DST-season local offset (~1 h) therefore
skews logs vs killmails — which also degrades the ±120 s time-window association.

**Proposed fix (own slice).** Detect the per-file offset by aligning the file's
log span to the overlapping fight window (the killmail-derived, authoritative
UTC time), snapping to whole-hour candidates; store the applied offset on
`GamelogFile` and surface it in the UI; re-bucket with corrected timestamps. Add
a regression test with a known-offset fixture. Until then the graph spans the
full engagement so markers remain visible.

## Phasing (each independently shippable, TDD, verified before "done")

1. **Access-control privacy** — small, security-critical, independent. Do first.
2. **Color / contrast** — small, quick win, independent.
3. **Data model + side resolver** — foundational for the graph.
4. **Fleet graph redesign** — largest; depends on (3).
5. **Timeline summary cards + inline expand** — depends on (4)'s side data and
   (1)'s redaction.
6. **Filtering toggles** — depends on the coverage/involvement plumbing.

Each phase: failing tests first → implement → full `pytest` + `vitest` green →
run the app and verify the actual behavior → `/code-review` → commit.

## Defaults chosen (flag if wrong)

- Smoothing **on by default**, raw via toggle.
- Anomaly series **hidden by default**, surfaced via the "Show anomalies" preset.
- Side palette leans **blue (friendly) / orange (hostile)** for colorblind safety,
  with red reserved for incoming-damage emphasis where it doesn't clash.

## References

- FFLogs pins / graph: https://www.fflogs.com/help/pins
- Warcraft Logs pins: https://www.warcraftlogs.com/help/pins
- Archon — How to Navigate Through Logs (FFXIV): https://www.archon.gg/ffxiv/articles/help/how-to-navigate-through-logs
- Grafana shared crosshair/tooltip: https://github.com/grafana/grafana/issues/97600
- Diverging charts best practice: https://www.domo.com/learn/charts/divergent-bar-charts
- br.evetools battle report tool: https://br.evetools.org/
