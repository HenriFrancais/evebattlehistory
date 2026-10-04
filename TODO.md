# TODO — after the 2026-10-03 review and fix pass

The first review (commit `0f00413`) listed 18 findings plus a low-severity list.
All of them were actioned on 2026-10-03, one squash commit per fix, each with
tests (see [Done](#done)). This file now holds what is still open: the steps
the next deploy needs, items a fix deliberately left out, and new findings from
a second review pass. Ordered by severity, most severe first.

Context used for ordering (unchanged): strict privacy (per-pilot log data and
user↔character mapping are FC/HC-only), members honest but careless, live
production with backups, tie-breaker member adoption / UX.

State at the end of the pass: backend 655 tests, frontend 197, `ruff check app
tests`, `mypy app`, `tsc` and the SPA build all pass; the Docker image builds
and a container was smoke-tested. Nothing has been pushed or deployed.

---

## Before and after the next deploy

These are actions, not code. The batch changes behaviour on first boot.

- [ ] **Take a manual backup first.** On first boot the app migrates the schema
      from version 0 to 4 and writes a full copy of the database to
      `<db>.pre-v0` in the data volume. Migrations 3 and 4 delete rows (log
      events with no effect; stored logs with no combat). Verified on a copy of
      the dev DB only (1.2 s, 1.05M → 0.90M events).
- [ ] **Reparse the logs once: `./deploy/deploy.sh --reparse`.** The dashboard
      overhaul (branch `feat/br-dashboard-overhaul`, migration 5) stores misses
      and each bucket's smallest and largest hit. Until the reparse runs, old
      battles show a dash for Min, Max and Misses in the timeline stats table.
      On a copy of the dev DB: 516 files in 3 min 22 s, 39,601 misses added.
- [ ] **Backfill tickers once:
      `docker compose exec nvbr python -m app.ingest.tickers`.** Corp and
      alliance tickers are fetched at ingest for new reports only; this fills
      the existing rows (554 entities in 7 s on the dev DB copy). Until then
      pilot names show without tickers.
- [ ] **Run `./deploy/deploy.sh --recompute` once.** It now re-aggregates every
      BR: stored side rollups move to the new classification and the headline
      follows the new "our kills" rule (see Done #8, #9). On the dev DB copy no
      win/loss result changed and all log stamps were preserved; production may
      differ.
- [ ] **Check `deploy/.env` before starting.** The app now refuses to start
      with a missing/default `NV_TOKEN`, or with `DEV_MODE=true` and a
      `URL_PREFIX`.
- [ ] **Set `ESI_USER_AGENT`** to include a maintainer contact. The default is
      the project URL, which I took from the README clone command.
- [ ] **Set `DISCORD_ALERT_CHANNEL_ID`** if failed backups should post to
      Discord. Without it a failure only shows in `docker compose logs backup`.
- [ ] **Run a backup, then `python -m app.backup --verify`.** The remote layout
      changed: logs now sync once into `<remote>/logs/`, snapshots hold only
      `app.db`. The first run uploads every log once. Old snapshots (with their
      own `logs/`) are still restorable and age out through `BACKUP_KEEP`.
- [ ] **Tell members the battle report page changed.** It is now tabbed
      (Involved, Timeline, Comms, AAR; Manage for FC/HC). Old links to a
      character's page redirect to the Timeline tab with that pilot isolated.
- [ ] **Tell members what changed.** Non-FC viewers no longer see other pilots'
      rows in the snapshot, reconcile and EWAR views, the coverage matrix, or
      named friendly leaders on the timeline hover.
- [ ] Remove `<db>.pre-v0` from the volume once the deploy is verified; it is
      as large as the database and is included in nothing else.

---

## High

### 1. Confirm the two privacy calls I made for you

The strict policy left two payloads undecided. I applied the conservative
reading; both are easy to relax.

- [ ] Timeline hover: friendly "top pilot" leaders are hidden from non-FC
      viewers unless the leader is their own character
      (`app/api/fleet.py`, `_leaders_out`). Enemy leaders stay visible.
- [ ] Fleet composition: `reps_out` and `has_logs` are zeroed for other pilots
      for non-FC viewers. Killmail-derived fields (damage, kills, weapons,
      ship) stay visible.

### 2. "Anonymous" performance distributions identify pilots in small groups

- [ ] `/performance` gives every member the fleet's per-pilot medians for
      target switching, logi response and broadcast lead
      (`app/analytics/performance.py:176`). With one or two logi pilots the
      "distribution" is that pilot's number. Suppress a distribution below a
      minimum group size (say 5), or bucket it.

### 3. A log event still belongs to one fight when kill sets differ

- [ ] Reports with the *same* killmails now share a fight and its logs, and a
      duplicate source is refused. Two reports that overlap in time with
      *different* kill sets (a `/related/` link and a wider window, say) still
      get separate fights, and each event goes to the lowest fight id. Fixing
      it properly needs a `log_event_fight` link table and a change to every
      `LogEvent.fight_id` query (about 30 sites).

---

## Medium

### 4. One worker means one slow request stalls everyone

The timeline, composition and BR list are now cached, and the worker timeout is
120 s. Still computed in Python on the event loop on every request:

- [ ] Whole-battle `/snapshot`: 1.2–1.3 s and 862 KB on the largest dev BR.
      Not cached (results are large and keyed by an arbitrary time range).
- [ ] `/performance` and `/broadcasts/metrics`: about 250 ms each, and
      `/performance` recomputes the composition instead of reusing the cache.
- [ ] Cold timeline (1.0 s) and cold composition (1.7 s) after any
      invalidation. The cache has one global version, so each log upload makes
      every BR cold again. Precompute at ingest, or scope invalidation per BR.

### 5. Logi response time only uses the recipient's log

- [ ] `_compute_reps` looks for the first rep in the *requester's* log
      (`app/analytics/broadcasts.py:622`). If the requester uploaded no log,
      the logi who answered gets no response time even when their own log has
      the outgoing rep. Use the applier's `out` rows as a fallback, as the
      snapshot already does.

### 6. Unused log events are still stored

- [ ] About half of the stored events are outside every fight window. They
      cannot simply be pruned: a battle report created later associates from
      stored rows. Pruning needs association to re-parse the overlapping raw
      files on demand. `VACUUM` after migration 3 would also reclaim the space
      the deleted rows used.

### 7. Parser miss rate is recorded but not shown

- [ ] `combat_lines` and `unmatched_combat` are on `/api/logs/mine` and in the
      DB, but nothing surfaces them. Show them on "My logs" and add an FC view
      of files with a high unmatched share, so a new log format gets noticed.
      Files uploaded before migration 3 have zeros until a reparse.

### 8. Frontend types are still hand-written

- [ ] `frontend/src/api.ts` mirrors `app/api/schemas.py` by hand, and
      `/coverage`, `/participants`, `/logs/mine` and the upload response return
      untyped dicts. Give those endpoints response models and generate the
      client types from the OpenAPI schema.

### 9. NPC names are not in the ship-name dictionary

- [ ] The SDE processing keeps published types only, so NPCs (Sleepers,
      Drifters) are missing: the dev DB has 423 ships and 8 entities. Their
      names are treated as pilot names and sent to ESI for resolution on every
      upload. Include the unpublished NPC groups relevant to wormholes.

### 10. No way to create a deliberate duplicate report from the UI

- [ ] A source already used by another report returns 409 with the existing
      report's id. The API accepts `allow_duplicate: true`; the create form has
      no control for it and does not link to the existing report.

---

## Low

- [ ] Rollups on a fight shared by two reports follow whichever report
      aggregated last. Only matters if the two reports carry different side
      overrides.
- [ ] Migration 4 deletes the rows of stored empty logs but leaves their files
      in `LOG_DIR`, where they are still backed up. Add a sweep for files no
      row references.
- [ ] Pre-migration snapshots (`<db>.pre-v<N>`) accumulate; nothing removes
      them.
- [ ] Timestamps are normalised at the API boundary and through
      `app/timeutil.py`, but columns are still a mix of naive and
      `timezone=True`. A single UTC column type would remove the remaining
      footgun.
- [ ] The test suite still emits about 220 "Event loop is closed" warnings at
      teardown; async engines are not disposed inside their own loop.
- [ ] `frontend/src/components/FleetGraph.tsx` (about 1,100 lines) and
      `BrDetailPage.tsx` (about 800) are overdue a split.
- [ ] Character identity for an upload comes from the filename's character id
      with no ownership check. Accepted under the honest-member threat model;
      revisit if that changes.
- [ ] A cold `/composition` after an upload re-derives off-BR participants for
      the BR because the off-BR cache is cleared wholesale on every upload.

---

## Not verified

- Whether the backup sidecar, running as root, can leave root-owned `-wal` /
  `-shm` files the app user cannot write. Check ownership in the volume on the
  VM after a backup run.
- The CI workflow has not run; it was only parsed locally.
- Production data volume and latency behind NV Tools and Caddy. All timings
  here are local and from the dev DB.
- `app/analytics/broadcasts.py`, `composition.py` and `weapons.py` were read for
  access control, side handling and the two metric issues above, not audited
  line by line for metric correctness.
- The fixes were exercised through the test suites, a copy of the dev DB and a
  container smoke test. Nothing was clicked through in a browser.

---

## Done

One squash commit per fix on `master`, oldest first.

| # | Commit | What |
|---|---|---|
| 1 | `cc5bb6f` | Per-pilot log data and user mapping gated to FC/HC |
| 2 | `b55ec98` | One gunicorn worker; off-BR cache cleared on upload |
| 3 | `977f27f` | Versioned schema migrations at startup, with a pre-migration snapshot |
| 4 | `2db8fa2` | Backups fail loudly, never prune after a failed push, store logs once |
| 5 | `576b0e1` | Upload: short transactions, deferred ESI, threaded parse, per-file UI |
| – | `155c951` | Test suite isolated from `.env` and real data (found during the pass) |
| 6 | `5a517e4` | Incomplete ingests are reported; upstream failures retried then raised |
| 7 | `66b290a` | Reports of the same engagement share one fight and its logs |
| 8 | `6968d4f` | One `SideResolver`; headline counts only kills we were on |
| 9 | `9662ba0` | Stored side rollups from classification; 2-colouring removed |
| 10 | `66b9f75` | gzip, derived-read cache, paged BR list |
| 11 | `b71989c` | Only effect events stored; parser stats kept; shared row builder |
| 12 | `c22096a` | Delete your own uploads; empty logs are not stored |
| 13 | `df4b44c` | Roster outage degrades instead of 500 |
| 14 | `8400429` | Broadcast log on several reports; ids kept on reparse; files cleaned up |
| 15 | `071afd1` | ESI limits on every call; zKillboard User-Agent; names not erased |
| 16 | `5015b24` | Unsafe auth config refused; real health check; honest image build |
| 17 | `05724f2` | One UTC helper module; API datetimes normalised |
| 18 | `1c72afe` | CI workflow; mypy and ruff pass; one `require_br` |
| Low | `b82c353` | Kill tooltip escaped; only http(s) source links |
| Low | `de22ca8` | Filter values validated and bounded; AAR length caps; safe reactions |
| Low | `2e9ea81` | Single-pass affiliation lookup; targeted known-name check |
| Low | `3e86855` | README drift; root-level player logs ignored |
| Review 2 | `892e017` | Composition cached; migration snapshot off the event loop |
| Review 2 | `6ad1e74` | Members told their snapshot covers only their own characters |
| Review 2 | `2b21ba6` | Cap/repair requests no longer counted as false broadcasts |
| Review 2 | `367f817` | Longer gunicorn worker timeout for the single worker |

Found and fixed along the way, beyond the original list:

- The test suite read the developer's `.env`, so it wrote into the real
  `var/db/dev.db` and ran with `DEV_MODE` on (`155c951`).
- The container copied the baked SDE with `cp -n`, so the ship-name dictionary
  never updated after the first deploy (`5015b24`).
- The maintenance CLIs did not apply schema migrations before running
  (`9662ba0`).
