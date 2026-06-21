import { describe, expect, it } from 'vitest'
import type { FleetTimeline, Leaders } from './api'
import { toFleetView } from './fleet'
import { renderHoverSummary } from './hoverSummary'

function mk(effect_type: string, direction: string, values: (number | null)[]) {
  const metric = ['scram', 'disrupt', 'jam'].includes(effect_type) ? 'count' : 'amount'
  return { key: `${effect_type}:${direction}`, effect_type, direction, metric, values }
}

const leaders0: Leaders = {
  top_dmg_taken: { name: 'Bob<evil>', ship: 'Tengu', amount: 12000 },
  top_rep_recv: { name: 'Alice', ship: 'Loki', amount: 8500 },
  top_dmg_dealt: { name: 'Charlie', ship: 'Muninn', amount: 3000 },
  top_rep_done: { name: 'Eve', ship: 'Scimitar', amount: 2200 },
}

const leaders1: Leaders = {
  top_dmg_taken: null,
  top_rep_recv: null,
  top_dmg_dealt: null,
  top_rep_done: null,
}

const fleet: FleetTimeline = {
  x: [1000, 1005],
  series: [
    mk('damage', 'out', [500, 300]),
    mk('damage', 'in', [200, 100]),
    mk('rep_armor', 'in', [150, 75]),
    mk('rep_shield', 'in', [50, 25]),
  ],
  kills: [],
  fights: [],
  bucket_seconds: 5,
  t_start: 1000,
  t_end: 1005,
  leaders: [],
}

describe('renderHoverSummary', () => {
  const view = toFleetView(fleet, { smooth: false })
  const leaders = [leaders0, leaders1]

  it('bucket 0 — prominent class for top receiver (dmg taken)', () => {
    const html = renderHoverSummary(view, leaders, 0)
    // Top damage receiver must appear with hover-tip-top class
    expect(html).toContain('hover-tip-top')
    // Their name must appear (and be HTML-escaped)
    expect(html).toContain('Bob&lt;evil&gt;')
  })

  it('bucket 0 — top receiver amount formatted', () => {
    const html = renderHoverSummary(view, leaders, 0)
    // 12000 -> "12.0k"
    expect(html).toContain('12.0k')
  })

  it('bucket 0 — top rep receiver appears with hover-tip-top class', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Alice')
    // Both primary leaders are in the top section
    expect(html).toContain('hover-tip-top')
  })

  it('bucket 0 — dealer appears with hover-tip-secondary class', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('hover-tip-secondary')
    expect(html).toContain('Charlie')
  })

  it('bucket 0 — repper appears with hover-tip-secondary class', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Eve')
  })

  it('bucket 1 (all-null leaders) — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 1)
    expect(html).toContain('no log data')
  })

  it('out-of-range idx — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 99)
    expect(html).toContain('no log data')
  })

  it('bucket 0 — includes side totals (dmg_out, dmg_in, rep_in)', () => {
    const html = renderHoverSummary(view, leaders, 0)
    // dmg_out idx 0 = 500 -> "500"
    expect(html).toContain('500')
    // dmg_in idx 0 = 200 (stored as -200 in view) -> "200"
    expect(html).toContain('200')
    // rep_in idx 0 = 150+50=200 (stored as -200) -> "200"
  })
})
