# TODO — critical review, 2026-10-03

Review of `master` at `9bfaa0d`, against the project goals in `README.md`
(reconstruct fights from zKillboard + gamelogs; fleet timeline, composition and
per-character drill-downs; embedded in NV Tools) and the context given for this
review:

- **Privacy policy:** strict — per-pilot log-derived data and user↔character
  mapping are FC/HC-only; members see fleet aggregates plus their own characters.
- **Threat model:** members are honest but careless.
- **Production:** live, real users, `gunicorn -w 4`, backup sidecar running.
- **Tie-breaker:** member adoption / UX.

Method: read all of `app/`, `deploy/`, `scripts/` and the frontend's API, cache
and rendering paths; ran the test suites, linters and type checkers; timed the
main read endpoints against a copy of `var/db/dev.db` (122 BRs, 1.05M log
events). Items are ordered by severity, most severe first. Figures quoted are
from that dev DB, not production.

Baseline: backend 556 tests pass (4m29s), frontend 184 pass, `tsc` clean,
`ruff check app` clean. `mypy app` fails with 16 errors and `ruff check tests`
with 91, despite the README listing both as passing checks. There is no CI.

---

## Critical

### 1. Per-pilot data is exposed to every member (violates the strict policy)

Only the per-character timeline, events, character snapshot and log download
call `can_view_character`. The same data, and the user↔character mapping, is
served ungated elsewhere:

- [x] `GET /api/brs/{id}/snapshot` (`app/api/fleet.py:132`) returns every
      pilot's source→target rows with `source_character_id`. It makes the gate
      on its per-character twin (`app/api/fleet.py:162`) bypassable.
- [x] `GET /api/brs/{id}/fights/{fid}/reconcile` and `/ewar`
      (`app/api/analytics.py:46`, `:85`) return per-character log damage, reps
      and cap totals with no user check at all.
- [x] `GET /api/brs/{id}/coverage` (`app/api/brs.py:855`) returns the full
      user → characters matrix. It is gated only in the UI
      (`frontend/src/views/BrDetailPage.tsx:501`).
- [x] `GET /api/brs/{id}/participants` (`app/api/brs.py:820`) returns
      `user_name` for every roster character.
- [x] `GET /api/brs/{id}/broadcasts` (`app/api/broadcasts.py:192`) returns raw
      named "needs armor/shield/cap" lines, although `_metrics_out` hides the
      same named rows from non-elevated users (`app/api/broadcasts.py:86`).
- [x] `GET /api/roster/users` (`app/api/roster.py:20`) returns the whole roster
      with ranks. It exists for the dev impersonation picker and should be
      DEV_MODE-only.
- [x] Decide whether two "public by design" payloads fit the strict policy:
      `leaders` in the fleet timeline name the top friendly pilot per 5s bucket
      (`app/analytics/fleet.py:804`), and `/composition` returns per-pilot
      `reps_out`, `has_logs` and weapons to everyone (`app/api/fleet.py:246`).
- [x] `current_user(request)  # auth check` (`app/api/brs.py:831`, `:865`) is a
      no-op; it checks nothing.

Fix: one shared dependency that yields `(acting_user, elevated,
own_character_ids)` and row-filters on the server; per-endpoint tests for a
non-elevated viewer. `tests/test_e2_access_control.py` only covers timeline and
events today.

---

## High

### 2. Four gunicorn workers defeat all in-process coordination

`deploy/Dockerfile:80` runs `-w 4`, but the app keeps coordination state in
module globals:

- [x] The ingest lock and coalescing sets (`app/ingest/jobs.py:23`) are
      per-process, so a refresh landing on another worker runs a second ingest
      of the same BR. This is the "database is locked" race the lock was added
      to stop.
- [x] `sweep_pending` runs in every worker's lifespan (`app/main.py:86`), so a
      BR interrupted by a restart is re-ingested four times concurrently.
- [x] `restore_if_empty`, `create_all` and the SDE load also run four times at
      boot (`app/main.py:69-80`). On a fresh VM that is four concurrent rclone
      pulls into the same DB file.
- [x] The off-BR cache is invalidated only in the worker that ran the ingest
      (`app/fights/offbr_cache.py`); the other three serve stale participants
      for up to 15 minutes. Log upload never invalidates it at all.

Fix: run one worker (the app is async and SQLite has one writer anyway), or
move job claiming and locking into the database.

### 3. No schema migrations on a live database

- [x] Schema is `Base.metadata.create_all` only (`app/db/engine.py:62`). New
      tables appear, new columns do not; they are documented as hand-run
      `ALTER TABLE` in comments (`app/db/models.py:116`, `:205`, `:471`).
      `deploy/deploy.sh` promises an automatic update. Any deploy that adds a
      column breaks production until someone runs SQL by hand.

Fix: Alembic, or a small versioned migration runner executed before the app
starts, with a snapshot taken first.

### 4. Backup failures are silent

- [x] `RcloneClient.push` logs a non-zero exit and returns
      (`app/backup.py:79-84`). `run_backup` then prunes old snapshots and
      returns the destination (`app/backup.py:165-169`), so the CLI prints
      "Backup complete" and exits 0. `scripts/backup-loop.sh` never sees a
      failure.
- [x] A partial push still creates a dated directory, which counts toward
      `BACKUP_KEEP` and can rotate out the last good snapshot.
- [x] Nothing verifies a snapshot or exercises restore. Do a restore drill.
- [x] Every run re-copies the whole log directory (299 MB locally) into a new
      dated folder; store logs once (content-addressed) and snapshot only the DB.

Fix: raise on rclone failure, skip prune after a failed push, exit non-zero,
post failures to Discord.

### 5. Log upload holds the write lock across network calls and blocks the event loop

- [x] In `upload_logs` (`app/api/logs.py:57-87`) each file is flushed, then
      associated, then `resolve_log_characters` makes up to three ESI calls,
      and only then commits. The SQLite write lock is held for the whole ESI
      round trip, stalling every other write (comments, reactions, other
      uploads, ingest). Uploads are also outside the ingest lock.
- [x] `parse_log` is CPU-bound and runs on the event loop; one 20 MB log
      freezes every request on that worker.
- [x] The uploader sends all files in a single request
      (`frontend/src/components/BulkUploader.tsx:65`) and they are processed
      one by one. A whole Gamelogs folder means one multi-minute request
      through two proxies with no progress shown.
- [x] The size limit is checked after the whole file is read into memory, and
      there is no cap on file count.

Fix: commit before ESI and resolve names in a background task; parse in
`asyncio.to_thread`; upload per file (or small batches) from the client with a
progress list.

### 6. Ingest reports "ready" when killmails are missing

- [x] `fetch_killmails` drops any killmail that fails (`app/esi/client.py:71-79`)
      while `km_count` is set from the requested refs
      (`app/ingest/pipeline.py:195`). The BR shows a full count with fewer
      kills, and nothing flags it.
- [x] A non-200 from zKillboard yields empty refs and the source is still
      marked `ok` (`app/ingest/sources/zkillboard.py:128-142`,
      `app/ingest/pipeline.py:135`). A rate-limited request produces an empty,
      "ready" BR.
- [x] Window sources skip failed hourly anchors and stop silently at 49
      (`app/ingest/sources/zkillboard.py:18`, `:184-192`).

Fix: record fetched vs expected, surface "N of M killmails" and a
partial/warning status, raise on non-200, retry with backoff.

### 7. A log event can belong to only one fight

- [x] `LogEvent.fight_id` is single-valued and stamped only when NULL, over
      fights fetched with no `ORDER BY` (`app/logs/associate.py:422-450`).
      Every BR mints its own `Fight` rows (`app/fights/aggregate.py:259`), so
      two BRs covering the same engagement compete and one shows no log data.
- [x] Refreshing a BR deletes and re-creates its fights with higher ids
      (`app/fights/aggregate.py:107-150`), so its logs can move to the other BR.
- [x] Nothing prevents creating the same BR twice. The dev DB has one duplicate
      pair, giving 3 overlapping fight pairs.

Fix: a `log_event_fight` link table (or resolve by time range at read time), or
share one `Fight` per (system, window) across BRs. Warn on create when a source
already exists.

---

## Medium

### 8. Side classification has several inconsistent implementations

- [x] `classify_entity` returns friendly/hostile/unassigned
      (`app/analytics/sides_config.py:34`); the timeline leaders map unassigned
      to hostile (`app/analytics/fleet.py:693`); the BR list counts every
      non-friendly pilot as enemy (`app/fights/timeline_rows.py:263`).
- [x] Per-character overrides (`BrCharSide`) are honoured only by composition
      and the BR list. Headline ISK, win/loss, kill markers and leaders ignore
      them, and `PUT …/participants/{id}/side` (`app/api/fleet.py:298`) does
      not recompute the outcome.
- [x] The headline counts unassigned losses as "our ISK destroyed"
      (`app/analytics/sides_config.py:187-192`), so third parties dying in a
      three-way fight inflate our efficiency and can flip a result to a win.

Fix: one per-BR side resolver used by every reader.

### 9. The legacy 2-colouring is still stored and still drives filters

- [x] `assign_sides` recolours every alliance of a colour, not just one
      component, when co-attackers disagree (`app/fights/sides.py:114-121`),
      which collapses both fleets onto one side.
- [x] Its output (`FightSide.side_kind`, `BrShipCount`, `FightShipCount`) feeds
      the ship filters (`app/analytics/filters.py:194`, `:285`) and is returned
      directly by `/api/fights/filter` (`app/api/filters.py:44-70`), so filter
      results disagree with the sides shown in the UI. Overrides never update
      these tables.

Fix: derive ship counts from the classified sides, then delete the colouring.

### 10. Read endpoints are heavy, uncompressed and unpaginated

Warm timings on the largest dev BR (183k events), single request:

| Endpoint | Time | Payload |
|---|---|---|
| `/api/brs` (all 122 BRs, enriched) | 472 ms | 77 KB |
| `/fleet-timeline` | 927 ms | 526 KB |
| `/snapshot` (whole battle) | 1201 ms | 862 KB |
| `/performance` | 270 ms | 26 KB |

- [x] No compression anywhere: no `GZipMiddleware` in `app/main.py`, no
      `encode` in `deploy/Caddyfile`. Cheapest win available.
- [x] `/api/brs` re-derives sides, pilots and coverage for every BR on every
      load, with no pagination (`app/api/brs.py:590`).
- [x] The aggregation is Python on the event loop, so one slow request blocks
      the worker. Cache per BR keyed on last ingest, or precompute at ingest.

### 11. `log_event` stores far more than is used

- [x] Every envelope line is persisted, including lines with no effect
      (`app/logs/parse.py:906-908`, `app/logs/ingest.py:209`): 147k of 1.05M
      rows have no effect type and 52% of rows are not in any fight.
      `log_event` plus its indexes is about 217 MB of a 277 MB database, and
      the raw files are kept anyway.
- [x] Parser quality stats (`unmatched_combat`) are computed and thrown away
      (`app/logs/parse.py:915-921`), so there is no way to see the miss rate
      per file or to notice a new log format.

Fix: persist only effect rows, store the stats on `gamelog_file`, prune rows
outside any fight window.

### 12. Members cannot manage their uploads, and empty logs look like failures

- [x] There is no delete or replace for an uploaded log (`app/api/logs.py`).
- [x] 1,406 of 2,722 files in the dev DB are "unresolved": sessions with no
      `Listener` header and 49 events between them. They are stored and listed
      as "character not matched", which reads as an error. Reject or label
      them "no combat in this file" and keep them out of "My logs".
- [x] Character identity comes from the filename's character id with no check
      that the uploader owns it (`app/logs/filename.py:87-94`). Acceptable
      under the honest threat model; revisit if that changes.

### 13. The home page depends on the NV Tools roster API

- [x] `enrich_br_rows`, `br_coverage` and `br_participants` call the roster
      store unguarded (`app/fights/timeline_rows.py:115`,
      `app/logs/coverage.py:98`, `app/fights/participants.py:98`). If the
      portal API is down when a worker cold-starts, `/api/brs` returns 500.
      Other callers already degrade; these should too.

### 14. Broadcast files

- [x] `sha256` is unique across all BRs, so uploading the same broadcast log to
      a second BR returns "duplicate" pointing at the first BR's file and
      attaches nothing (`app/logs/broadcast_ingest.py:164-178`).
- [x] Reparse uses a lookup that always returns `None`
      (`app/logs/broadcast_reparse.py:26-32`), so subjects known only from the
      roster lose their `subject_character_id`.
- [x] Deleting a file or a BR leaves the stored file on disk.

### 15. Upstream API handling

- [x] The `/universe/names`, `/universe/ids` and `/characters/affiliation`
      POSTs bypass `_get`, so they have no 420/429 handling
      (`app/esi/client.py:93`, `:144`, `:168`, `:196`). `_get` retries once.
- [x] zKillboard requests send `User-Agent: nv-br` with no contact and no
      backoff (`app/ingest/sources/factory.py:129`,
      `app/ingest/sources/zkillboard.py:124`, `app/ingest/zkb_value.py:59`).
- [x] When ESI name resolution fails, the upserts overwrite existing alliance,
      corporation and character names with NULL (`app/ingest/persist.py:153-208`).

### 16. Start-up and build safety

- [x] If `NV_TOKEN` is missing the default `dev-token-change-me` is accepted
      (`app/config.py:39`), and `DEV_MODE=true` bypasses auth with no guard
      (`app/middleware.py:53`). Refuse to start with either when
      `DATA_SOURCE=real` or `URL_PREFIX` is set.
- [x] `deploy/Dockerfile:55-58` chains `sde.refresh && mkdir && cp … || true`,
      which swallows an SDE download failure. The image then ships without the
      ship-name dictionary and pilot/ship splitting quietly degrades
      (`app/logs/ingest.py:166`).
- [x] The image installs unpinned dependencies; `uv.lock` is ignored
      (`deploy/Dockerfile:34-39`).
- [x] `config.toml` is not copied into the image, so `create_ranks` and
      `create_teams` cannot be changed in production without a code change.

### 17. Timestamps

- [x] SQLite drops tzinfo on write, and naive values are read back as UTC. A
      window source sent with a non-UTC offset is stored as wall-clock and then
      queried an offset off (`app/ingest/sources/zkillboard.py:21`). The
      frontend sends `Z` today; the API does not enforce it.
- [x] `_as_utc` / `_epoch` are copy-pasted in six modules. Normalise once at
      the schema boundary with a UTC column type.

### 18. Engineering hygiene

- [x] No CI. Add one running pytest, ruff, mypy, vitest and `tsc`, and make
      mypy and `ruff check tests` pass or stop claiming them.
- [x] The event-cleaning block is duplicated between `app/logs/ingest.py:161-206`
      and `app/logs/reparse.py:66-109`. A parser fix applied to one silently
      diverges from the other.
- [x] `frontend/src/api.ts` (1,032 lines) hand-mirrors `app/api/schemas.py`,
      and several endpoints return untyped dicts. Generate the client types
      from the OpenAPI schema.
- [x] The backend suite takes 4.5 minutes and emits "Event loop is closed"
      thread warnings; engines are not disposed between tests.
- [x] `_require_br` is defined three times and inlined about ten more.
      `FleetGraph.tsx` is 1,118 lines.

---

## Low

- [x] The kill tooltip builds `innerHTML` from unescaped pilot and ship names
      (`frontend/src/components/FleetGraph.tsx:119-131`); the hover summary
      already has an `esc()` helper. Names come from ESI/SDE, so risk is small.
- [x] `source_url` is rendered as an `href` with no scheme check
      (`frontend/src/views/BrDetailPage.tsx:652`) and link sources accept any
      URL (`app/api/brs.py:70-78`). Host matching is a substring test
      (`app/ingest/sources/factory.py:82-85`).
- [x] The filter API passes unvalidated value types to SQL operators (500
      instead of 400), allows unbounded nesting, and `/api/fights/filter` has
      no result limit (`app/analytics/filters.py`).
- [x] AAR and comment bodies have no length cap (`app/api/schemas.py:808-813`).
- [x] Two simultaneous reaction toggles hit the unique constraint and return
      500 (`app/api/aar.py:368-391`).
- [x] The 401 response carries no CSP header (`app/middleware.py:67`), and the
      bearer comparison is not constant-time.
- [x] `/healthz` does not touch the database and reports per-worker roster
      state.
- [ ] `persist_killmails` looks up each corp and character by scanning every
      killmail (`app/ingest/persist.py:25-46`), and `resolve_log_characters`
      loads every character name on each upload
      (`app/fights/offbr_resolve.py:54-59`).
- [ ] README drift: the `config.toml` sample differs from the committed file;
      it says cross-app links use `target="_top"` but the code uses `_blank`;
      the "Updating a deployment" section appears twice with different steps.
- [ ] Repo hygiene: a real fleet-broadcast log sits untracked at the repo root
      and `.gitignore` only covers digit-named gamelogs, so `git add -A` would
      commit player data. Move it under `var/` or ignore `/*.txt`.

---

## Not verified

- Whether the backup sidecar running as root can leave root-owned `-wal`/`-shm`
  files that the app user cannot write. Worth a check on the VM.
- Production data volume and real endpoint latency behind NV Tools and Caddy;
  the timings above are local and warm.
- `app/analytics/broadcasts.py`, `performance.py`, `composition.py` and
  `weapons.py` were checked for access gating and side handling only, not
  line-by-line for metric correctness.
