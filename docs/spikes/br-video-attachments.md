# Spike: BR video attachments (Google Drive) + timeline↔video sync

**Date:** 2026-06-22 · **Status:** Feasibility explored, not yet designed. No code written.
Captured for future development.

## The idea
1. When a BR is created, create a folder for it on the linked Google Drive.
2. Users can upload a video file through the BR; it lands in that folder.
3. Users can watch those videos inside the BR.
4. **Extension:** link the log-timeline view to the video so the timeline plays/scrubs
   in sync with the video, exploring footage and combat log together.

## Verdict
**All four pieces are feasible.** At our actual volume the cost is effectively zero on the
VM we already run, and — importantly — the architecture that satisfies our privacy rule is
also the one that *enables* the timeline-sync extension. Nothing in the constraints forces
us to give up the sync feature.

The route that survives every constraint: **store videos privately in Drive, proxy the
bytes through our FastAPI backend (gated by the existing `X-User-*` headers), play in a
real `<video>` element, and drive a synced playhead on the existing epoch-second timeline
axis.**

---

## Why this architecture (the reasoning, so we don't relitigate it)

### Access control is the decider, and it rules out the easy Drive routes
We have two different identity systems that don't line up:
- **NV Tools identity** = `X-User-*` headers / corp ranks & teams (what the app knows).
- **Google identity** = Google accounts / Groups (what Drive knows).

Our rule is: *anyone logged into NV Tools may watch; nobody external may.* Drive can only
enforce **its own** gate (Google identity), so letting Drive serve the bytes directly
(preview iframe or share-link) forces a bad choice:
- **Link-share the file** → the file id is in the page source; a member can copy it and
  forward it externally → **leaks to external users.** Fails the rule.
- **Add every NV Tools user to a Google Group** → a two-identity sync problem; needs
  Google Workspace for programmatic membership (Admin SDK); our consumer **AI Plus**
  account can't do it cleanly; and members must hand over a personal Gmail (PII / social
  friction for pseudonymous EVE players).

**Conclusion:** the only clean way to honour "NV-Tools-login = yes, external = no" is to
make the **backend the gate**. Files stay fully private (owned by one Google account, never
shared). The backend authenticates to Drive as that one account via an OAuth refresh token
(same credential style as the existing rclone backup, see `app/backup.py`), checks the
`X-User-*` headers per request, and streams the bytes only to valid NV Tools users.
External users never reach the backend (they can't pass the NV Tools proxy).

### The "let Google serve it for free" idea is intrinsically incompatible with our privacy rule
The Drive **preview iframe** authenticates via the *user's own browser→Google session*,
which the VM cannot supply on the user's behalf. A VM-held account does **not** help,
because the iframe runs in the user's browser, not on the VM. More fundamentally:
**"Google serves the bytes for free" and "Google doesn't know our users" are mutually
exclusive** — if Google can't identify a user, it can only serve them by leaving the file
open (a leak). Privacy ⟹ we serve the bytes. That's a hard constraint, not a missing trick.

(The Gmail-collection + Google-Group route *would* make Drive preview work, but it depends
on third-party cookies in a doubly-nested iframe — already broken in Safari, deprecating in
Chrome — needs Workspace for sync, weakens containment, asks for PII, **and re-kills the
timeline-sync feature.** Rejected.)

### Privacy and the sync feature point the same way
Timeline sync needs to read `video.currentTime`, which is **impossible** from a cross-origin
Drive iframe but **trivial** from a `<video>` element we own. The privacy requirement
already commits us to the proxied-`<video>` route — so it enables sync for free rather than
fighting it. Earlier these two pulled apart; with our constraints they align.

---

## What the codebase already gives us (makes this tractable)
- **Timeline is already in absolute UTC epoch seconds.** `CharacterTimeline.x`,
  `t_start`/`t_end` (`app/analytics/timeline.py`); `FleetGraph` already has a
  `selectedRange` scrubbing concept (`frontend/src/components/FleetGraph.tsx`,
  `frontend/src/timeline.ts`). The sync math is nearly a gift:
  `playhead_utc = video.currentTime + anchorOffset`, driven on the video's `timeupdate`
  event, plotted on the axis the chart already draws.
- **BRs have a stable UUID** (`br_id`) — natural folder key.
- **Auth + permission gating exist** (`app/api/auth.py` `can_create_br`, `current_user`;
  `X-User-*` headers via `app/middleware.py`).
- **A Drive credential already exists** (rclone backup, `app/backup.py`) — reuse the same
  OAuth refresh-token style; narrow scope to a dedicated app folder.
- **A file-upload endpoint pattern exists** (`app/api/logs.py`) — but it's capped at 20 MB
  and streams through the backend to local disk; videos need a different (larger, possibly
  resumable) path.

---

## Remaining real work (the honest costs)
1. **Backend streaming proxy with HTTP range-request (206) support.** Without byte-range
   passthrough to Drive's media API, seeking/scrubbing breaks — and sync *is* seeking.
   This is the non-obvious must-have. Add a small (~few GB) LRU disk cache of hot clips to
   dodge Drive's per-file download-quota throttling (the CX box has only ~40 GB disk, so do
   **not** cache masters locally — masters live in Drive).
2. **Transcoding.** Browsers reliably play only mp4-h264 / webm. ShadowPlay already outputs
   mp4-h264, so if we **standardise/encourage mp4-h264 uploads**, transcoding is rare.
   Otherwise run `ffmpeg` as a low-priority one-time background job per upload (it's
   per-*upload*, not per-*view*). At our volume this is ~1–2 CPU-hours/month — negligible.
3. **Time anchor for sync.** Video files carry no reliable capture timestamp. Establish the
   offset with a one-time **manual "align" gesture** (drag the playhead to a known killmail
   flash); store the offset with the video. Once set, sync is solid.
4. **Large-upload UX** (progress, retry; possibly resumable / direct-to-Drive) — scope when
   we actually build it.

---

## Cost sizing (based on our real numbers)

**Inputs:** 65 videos over 3 years → ~22/yr; assume **2×** → **~43/yr ≈ ~3.6/month**.
~1 GB / ~15 min per clip (ShadowPlay 1080p). 10 views/video, watching ~50% (~0.5 GB,
~7.5 min per view). Host: **Hetzner CX-class box** (2 vCPU / 4 GB, **20 TB** included
traffic) — already running. Storage: existing **2 TB** Google AI Plus Drive (sunk).

Derived:
- Monthly egress ≈ 3.6 × 5 GB = **~20 GB/month** (0.1% of the 20 TB allowance).
- Storage growth ≈ **~43 GB/year** → **~45 years** of runway on 2 TB.
- Transcode ≈ **~1–2 CPU-hours/month** (zero if uploads are already mp4-h264).

| | **Self-host on our Hetzner box** | **Cloudflare Stream (compare)** |
|---|---|---|
| Monthly cost | **≈ €0 marginal** (box already paid) | ~$6 now → ~$15/mo as catalog grows |
| Egress | 0.1% of 20 TB used | n/a |
| Storage runway | ~45 yr on 2 TB | pay-as-you-grow |
| Transcode | ~1–2 CPU-hr/mo (trivial) | included |
| Build effort | we build proxy + transcode plumbing | turnkey |
| Privacy gate | `X-User` headers | signed tokens |
| **Timeline sync** | ✅ | ✅ (HLS into our own player) |

**Note:** the cheap-VM worry that motivated the CDN question **does not apply at our
volume** — ~4 videos/month and ~20 GB/month egress barely register on the CX box. The only
thing a CDN (Cloudflare Stream / Mux) really buys is *not building the proxy+transcode
plumbing ourselves* — a one-time engineering cost, ~$6–15/mo recurring. Both options keep
the sync feature. Host egress pricing is what would have changed the answer: on AWS/GCP
(~$85/TB egress) a CDN becomes competitive; on Hetzner (egress bundled) self-host wins
decisively.

## Recommendation when we return to this
Self-host on the existing Hetzner box: **private Drive storage + backend `<video>` proxy
(with range support) gated by `X-User-*` headers**, mp4-h264 upload standard with optional
background `ffmpeg`, then layer the **manual-anchor timeline sync** on top of the existing
epoch-second `FleetGraph`. Effectively €0/month, honours the privacy rule, and is the only
route that keeps the timeline-sync extension alive.

## Open questions for the design phase
- Reuse the existing rclone OAuth credential or mint a dedicated, app-folder-scoped one?
- One video per BR, per fight, or many (multiple POVs)? Affects folder layout & UI.
- Upload path: proxy-through-backend (simple) vs resumable/direct-to-Drive (scales)? At
  ~4/month, simple is almost certainly fine.
- Enforce mp4-h264 at upload, or accept-and-transcode? Probably accept-and-transcode with a
  nudge, given low volume.
