# Spike: br.evetools.org (Aurora) Battle Report API

**Date:** 2026-06-17 · **Status:** API contract confirmed; live 200 body still to capture.

## What we confirmed
The evetools "Aurora" BR tool is a SPA on `https://br.evetools.org`. `/br/<id>` is a
**client-side route**, not an API. The data layer is a JSON API on the same origin
(axios default `baseURL: "https://br.evetools.org"`), discovered in the bundle
`main.30f4e9d0…js`:

| Call | Method/URL | Notes |
|---|---|---|
| Battle report | `GET /br/battle/<reportId>` | The stored report (the `/br/<id>` page loads this). |
| Related kills | `GET /br/related/<systemId>/<YYYYMMDDHHMM>` | zKB-style related window. |
| Single killmail | `GET /killmail/<killId>` | |
| Battle list | `GET /br/battles?<query>` | |

These paths return `application/json` (e.g. `{"message":"Not Found","statusCode":404}`)
— distinct from the SPA's HTML fallback — so the routing is real API.

The killboard host is separate: `https://kb.evetools.org` (`/kill/<id>/`,
`/related/<sys>/<time>/`). Type/portrait images: `https://img.evetools.org/sdeimages/...`.

## Key risk surfaced
The example report `6a2ef28ca612848f41344503` **and** arbitrary `/br/related/...` windows
all returned **404** during the spike. evetools prunes/expires stored reports, so a
**user-pasted Aurora report id is not durable** — by the time an FC links it and we ingest,
or on a later re-ingest/restore, it may be gone.

**Implication for the design:** keep **zKBoard the robust primary** resolver (system+time →
ESI), and treat Aurora as best-effort enrichment. Critically, **persist the resolved killmail
id+hash set in our own DB at ingest time** so a BR never depends on the upstream report still
existing. This already aligns with the plan (immutable killmail cache + `br_killmail`).

## Open item (do at implementation, ideally from a browser)
Capture a **live 200 JSON body** from a freshly-created Aurora report (open a real report in
a browser, copy the `/br/battle/<id>` response from the Network tab) to pin the exact field
names (kill id+hash list, team/side composition). Save it as
`data_demo/aurora_br_<id>.json` for the demo source + parser tests. Until then the `aurora.py`
resolver is written against the confirmed URL contract with the response shape TBD-from-fixture.
