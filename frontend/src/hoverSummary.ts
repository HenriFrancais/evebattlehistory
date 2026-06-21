// Pure HTML builder for the cursor hover-summary tooltip.
// No uPlot import — unit-tested in jsdom.
//
// Shows three side-aware per-bucket leaders (all by TARGET/receiver):
//   1. Friendly target receiving the most hostile damage (top_friendly_dmg_taken)
//   2. Hostile target receiving the most friendly damage (top_hostile_dmg_taken)
//   3. Friendly target receiving the most friendly reps  (top_friendly_rep_recv)
//
// The `view` parameter is retained for API compatibility with the FleetGraph
// plugin wiring, but is no longer used for side-total computation.

import type { Leaders } from './api'
import type { FleetView } from './fleet'
import { fmtCompact } from './format'

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function allNull(l: Leaders): boolean {
  return (
    l.top_friendly_dmg_taken == null &&
    l.top_hostile_dmg_taken == null &&
    l.top_friendly_rep_recv == null
  )
}

function leaderLine(label: string, e: { name: string; ship: string | null; amount: number }): string {
  return (
    `<div class="hover-tip-top">` +
    `<span class="hover-tip-label">${label}:</span> ` +
    `<strong>${esc(e.name)}</strong>` +
    (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
    ` <span class="hover-tip-amount">${fmtCompact(e.amount)}</span>` +
    `</div>`
  )
}

/**
 * Render an HTML string for the hover tooltip at `idx`.
 *
 * Shows only the 3 side-aware receiver leaders:
 *   - "Friendly taking most damage: <name> (<ship>) <amount>"
 *   - "Hostile taking most damage: <name> (<ship>) <amount>"
 *   - "Friendly receiving most reps: <name> (<ship>) <amount>"
 *
 * Returns a "no log data" line when the bucket has no leader data.
 * The `view` parameter is unused but kept for FleetGraph plugin compatibility.
 */
export function renderHoverSummary(_view: FleetView, leaders: Leaders[], idx: number): string {
  const entry = leaders[idx]
  if (!entry || allNull(entry)) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  let html = ''
  if (entry.top_friendly_dmg_taken) {
    html += leaderLine('Friendly taking most damage', entry.top_friendly_dmg_taken)
  }
  if (entry.top_hostile_dmg_taken) {
    html += leaderLine('Hostile taking most damage', entry.top_hostile_dmg_taken)
  }
  if (entry.top_friendly_rep_recv) {
    html += leaderLine('Friendly receiving most reps', entry.top_friendly_rep_recv)
  }

  // If all were non-null in entry but rendered nothing (defensive), show fallback.
  if (!html) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  return html
}
