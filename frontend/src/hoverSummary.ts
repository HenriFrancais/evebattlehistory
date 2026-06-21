// Pure HTML builder for the cursor hover-summary tooltip.
// No uPlot import — unit-tested in jsdom.
//
// Shows side totals (DPS out / damage taken / reps received) from the FleetView
// family series at the hovered bucket index, plus the top individual leaders
// from the per-bucket leaders array (tasks 11-12).
//
// Top targets-under-pressure (dmg_taken receiver + rep receiver) are given the
// primary class `hover-tip-top`; top dealers/reppers get `hover-tip-secondary`.

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
    l.top_dmg_taken == null &&
    l.top_rep_recv == null &&
    l.top_dmg_dealt == null &&
    l.top_rep_done == null
  )
}

/** Find a series value at idx by family key, across all panels. Returns 0 if absent. */
function seriesVal(view: FleetView, key: string, idx: number): number {
  for (const panel of view.panels) {
    const s = panel.series.find((ps) => ps.key === key)
    if (s) {
      const v = s.values[idx]
      return v == null ? 0 : v
    }
  }
  return 0
}

/**
 * Render an HTML string for the hover tooltip at `idx`.
 *
 * Side totals are read from the FleetView (family series `dmg_out`, `dmg_in`,
 * `rep_in`). Incoming series are mirrored negative — we display magnitudes.
 *
 * Leaders layout:
 *   - `hover-tip-top`: top_dmg_taken (main pressure target) + top_rep_recv
 *   - `hover-tip-secondary`: top_dmg_dealt + top_rep_done
 *
 * Returns a "no log data" line when the bucket has no leader data.
 */
export function renderHoverSummary(view: FleetView, leaders: Leaders[], idx: number): string {
  const entry = leaders[idx]
  if (!entry || allNull(entry)) {
    return '<span class="hover-tip-no-data">no log data</span>'
  }

  // Side totals (magnitudes — incoming series are stored as negative)
  const dmgOut = Math.abs(seriesVal(view, 'dmg_out', idx))
  const dmgIn = Math.abs(seriesVal(view, 'dmg_in', idx))
  const repIn = Math.abs(seriesVal(view, 'rep_in', idx))

  const totals =
    `<div class="hover-tip-totals">` +
    `<span class="hover-tip-total-item">DPS out: <strong>${fmtCompact(dmgOut)}</strong></span>` +
    `<span class="hover-tip-total-item">Dmg in: <strong>${fmtCompact(dmgIn)}</strong></span>` +
    `<span class="hover-tip-total-item">Rep in: <strong>${fmtCompact(repIn)}</strong></span>` +
    `</div>`

  // Primary leaders: targets under pressure
  let top = ''
  if (entry.top_dmg_taken) {
    const e = entry.top_dmg_taken
    top +=
      `<div class="hover-tip-top">` +
      `<span class="hover-tip-label">Dmg recv:</span> ` +
      `<strong>${esc(e.name)}</strong>` +
      (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
      ` <span class="hover-tip-amount">${fmtCompact(e.amount)}</span>` +
      `</div>`
  }
  if (entry.top_rep_recv) {
    const e = entry.top_rep_recv
    top +=
      `<div class="hover-tip-top">` +
      `<span class="hover-tip-label">Rep recv:</span> ` +
      `<strong>${esc(e.name)}</strong>` +
      (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
      ` <span class="hover-tip-amount">${fmtCompact(e.amount)}</span>` +
      `</div>`
  }

  // Secondary leaders: top contributors
  let secondary = ''
  if (entry.top_dmg_dealt) {
    const e = entry.top_dmg_dealt
    secondary +=
      `<div class="hover-tip-secondary">` +
      `<span class="hover-tip-label">Top dmg:</span> ` +
      `${esc(e.name)}` +
      (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
      ` ${fmtCompact(e.amount)}` +
      `</div>`
  }
  if (entry.top_rep_done) {
    const e = entry.top_rep_done
    secondary +=
      `<div class="hover-tip-secondary">` +
      `<span class="hover-tip-label">Top rep:</span> ` +
      `${esc(e.name)}` +
      (e.ship ? ` <span class="hover-tip-ship">(${esc(e.ship)})</span>` : '') +
      ` ${fmtCompact(e.amount)}` +
      `</div>`
  }

  return totals + top + secondary
}
