// Pure HTML builder for the cursor hover-summary tooltip.
// No uPlot import — unit-tested in jsdom.
//
// The leaders shown depend on WHICH panel is hovered:
//   damage panel → friendly/hostile taking most damage, friendly receiving most reps
//   cap panel    → hostile under / friendly applying most cap pressure (neut+nos), friendly receiving most cap
//   ewar panel   → hostile most tackled, friendly most tackled
// Each is the top character for that metric in the hovered time bucket.

import type { LeaderEntry, Leaders } from './api'
import type { PanelId } from './fleet'
import { fmtCompact } from './format'

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// The leader fields shown for each panel, in display order, with their labels.
const PANEL_LEADERS: Record<PanelId, { key: keyof Leaders; label: string }[]> = {
  damage: [
    { key: 'top_friendly_dmg_taken', label: 'Friendly taking most damage' },
    { key: 'top_hostile_dmg_taken', label: 'Hostile taking most damage' },
    { key: 'top_friendly_rep_recv', label: 'Friendly receiving most reps' },
  ],
  cap: [
    { key: 'top_hostile_cap_pressure', label: 'Hostile under most cap pressure' },
    { key: 'top_friendly_cap_pressure', label: 'Friendly applying most cap pressure' },
    { key: 'top_friendly_cap_recv', label: 'Friendly receiving most cap' },
  ],
  ewar: [
    { key: 'top_hostile_tackle_taken', label: 'Hostile most tackled' },
    { key: 'top_friendly_tackle_taken', label: 'Friendly most tackled' },
  ],
}

function leaderLine(label: string, e: LeaderEntry): string {
  const icon =
    e.ship_type_id != null
      ? `<img class="hover-tip-ship-icon" src="https://images.evetech.net/types/${e.ship_type_id}/icon?size=32" width="16" height="16" alt="" />`
      : ''
  return (
    `<div class="hover-tip-top">` +
    `<span class="hover-tip-label">${label}:</span> ` +
    icon +
    `<strong>${esc(e.name)}</strong>` +
    (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
    ` <span class="hover-tip-amount">${fmtCompact(e.amount)}</span>` +
    `</div>`
  )
}

/**
 * Render an HTML string for the hover tooltip at `idx`, showing the leaders
 * relevant to `panelId`. Returns a "no log data" line when the bucket has no
 * leader data for that panel.
 */
export function renderHoverSummary(panelId: PanelId, leaders: Leaders[], idx: number): string {
  const entry = leaders[idx]
  if (!entry) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  let html = ''
  for (const { key, label } of PANEL_LEADERS[panelId]) {
    const e = entry[key]
    if (e) html += leaderLine(label, e)
  }

  if (!html) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }
  return html
}
