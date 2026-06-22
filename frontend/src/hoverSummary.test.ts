import { describe, expect, it } from 'vitest'
import type { FleetTimeline, Leaders } from './api'
import { toFleetView } from './fleet'
import { renderHoverSummary } from './hoverSummary'

function mk(effect_type: string, direction: string, values: (number | null)[]) {
  const metric = ['scram', 'disrupt', 'jam'].includes(effect_type) ? 'count' : 'amount'
  return { key: `${effect_type}:${direction}`, effect_type, direction, metric, values }
}

const leadersPopulated: Leaders = {
  top_friendly_dmg_taken: { name: 'Bob<evil>', ship: 'Tengu', amount: 12000, ship_type_id: 29984 },
  top_hostile_dmg_taken: { name: 'EnemyAce', ship: 'Loki', amount: 9000, ship_type_id: 29990 },
  top_friendly_rep_recv: { name: 'Alice', ship: 'Scimitar', amount: 8500, ship_type_id: 11978 },
}

const leadersAllNull: Leaders = {
  top_friendly_dmg_taken: null,
  top_hostile_dmg_taken: null,
  top_friendly_rep_recv: null,
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
  const leaders = [leadersPopulated, leadersAllNull]

  it('bucket 0 — friendly dmg target has prominent class and escaped name', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('hover-tip-top')
    expect(html).toContain('Bob&lt;evil&gt;')
    expect(html).toContain('Friendly taking most damage')
  })

  it('bucket 0 — hostile dmg target appears with label', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('EnemyAce')
    expect(html).toContain('Hostile taking most damage')
  })

  it('bucket 0 — friendly rep target appears with label', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Alice')
    expect(html).toContain('Friendly receiving most reps')
  })

  it('bucket 0 — amounts formatted with fmtCompact', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('12.0k')  // 12000 → "12.0k"
    expect(html).toContain('9.0k')   // 9000  → "9.0k"
    expect(html).toContain('8.5k')   // 8500  → "8.5k"
  })

  it('bucket 0 — ship names appear in output', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('Tengu')
    expect(html).toContain('Loki')
    expect(html).toContain('Scimitar')
  })

  it('bucket 0 — renders a ship icon img for each entry from ship_type_id', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).toContain('hover-tip-ship-icon')
    expect(html).toContain('https://images.evetech.net/types/29984/icon')
    expect(html).toContain('https://images.evetech.net/types/29990/icon')
    expect(html).toContain('https://images.evetech.net/types/11978/icon')
  })

  it('bucket 0 — does NOT contain old dealer/repper labels', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).not.toContain('Top dmg:')
    expect(html).not.toContain('Top rep:')
    expect(html).not.toContain('Dmg recv:')
    expect(html).not.toContain('Rep recv:')
  })

  it('bucket 0 — does NOT contain side-total lines', () => {
    const html = renderHoverSummary(view, leaders, 0)
    expect(html).not.toContain('DPS out:')
    expect(html).not.toContain('Dmg in:')
    expect(html).not.toContain('Rep in:')
  })

  it('bucket 1 (all-null) — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 1)
    expect(html).toContain('no log data')
  })

  it('out-of-range idx — returns "no log data"', () => {
    const html = renderHoverSummary(view, leaders, 99)
    expect(html).toContain('no log data')
  })
})
