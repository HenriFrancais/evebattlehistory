import { describe, expect, it } from 'vitest'
import type { Leaders } from './api'
import { renderHoverSummary } from './hoverSummary'

const leadersPopulated: Leaders = {
  top_friendly_dmg_taken: { name: 'Bob<evil>', ship: 'Tengu', amount: 12000, ship_type_id: 29984 },
  top_hostile_dmg_taken: { name: 'EnemyAce', ship: 'Loki', amount: 9000, ship_type_id: 29990 },
  top_friendly_rep_recv: { name: 'Alice', ship: 'Scimitar', amount: 8500, ship_type_id: 11978 },
  top_hostile_cap_pressure: { name: 'EnemyCap', ship: 'Bhaalgorn', amount: 3000, ship_type_id: 17920 },
  top_friendly_cap_pressure: { name: 'NeutBro', ship: 'Legion', amount: 4200, ship_type_id: 29986 },
  top_friendly_cap_recv: { name: 'CapMe', ship: 'Guardian', amount: 1500, ship_type_id: 11987 },
  top_hostile_tackle_taken: { name: 'TackledEnemy', ship: 'Sabre', amount: 3, ship_type_id: 22456 },
  top_friendly_tackle_taken: { name: 'PinnedFriend', ship: 'Loki', amount: 2, ship_type_id: 29990 },
}

const leadersAllNull: Leaders = {
  top_friendly_dmg_taken: null,
  top_hostile_dmg_taken: null,
  top_friendly_rep_recv: null,
}

const leaders = [leadersPopulated, leadersAllNull]

describe('renderHoverSummary — damage panel', () => {
  it('shows the three damage/rep leaders with escaped name + ship icons', () => {
    const html = renderHoverSummary('damage', leaders, 0)
    expect(html).toContain('Bob&lt;evil&gt;')
    expect(html).toContain('Friendly taking most damage')
    expect(html).toContain('Hostile taking most damage')
    expect(html).toContain('Friendly receiving most reps')
    expect(html).toContain('12k')
    expect(html).toContain('https://images.evetech.net/types/29984/icon')
  })

  it('does NOT show cap/tackle leaders on the damage panel', () => {
    const html = renderHoverSummary('damage', leaders, 0)
    expect(html).not.toContain('most neut')
    expect(html).not.toContain('tackled')
  })
})

describe('renderHoverSummary — cap panel', () => {
  it('shows the three cap leaders only', () => {
    const html = renderHoverSummary('cap', leaders, 0)
    expect(html).toContain('Hostile under most cap pressure')
    expect(html).toContain('EnemyCap')
    expect(html).toContain('Friendly applying most cap pressure')
    expect(html).toContain('NeutBro')
    expect(html).toContain('Friendly receiving most cap')
    expect(html).toContain('CapMe')
    expect(html).not.toContain('most damage')
    expect(html).not.toContain('tackled')
  })
})

describe('renderHoverSummary — ewar panel', () => {
  it('shows the two tackle leaders only', () => {
    const html = renderHoverSummary('ewar', leaders, 0)
    expect(html).toContain('Hostile most tackled')
    expect(html).toContain('TackledEnemy')
    expect(html).toContain('Friendly most tackled')
    expect(html).toContain('PinnedFriend')
    expect(html).not.toContain('most damage')
    expect(html).not.toContain('most neut')
  })
})

describe('renderHoverSummary — empty cases', () => {
  it('all-null bucket returns "no log data"', () => {
    expect(renderHoverSummary('damage', leaders, 1)).toContain('no log data')
  })

  it('cap panel with no cap leaders returns "no log data"', () => {
    // bucket 0 has no cap fields if we look at the all-null entry
    expect(renderHoverSummary('cap', leaders, 1)).toContain('no log data')
  })

  it('out-of-range idx returns "no log data"', () => {
    expect(renderHoverSummary('damage', leaders, 99)).toContain('no log data')
  })
})

describe('renderKillTip', () => {
  const kill = {
    ts: 1_750_000_000,
    killmail_id: 1,
    victim_character_id: 5,
    victim_character_name: '<img src=x onerror=alert(1)>',
    victim_ship_name: 'Loki <b>bold</b>',
    victim_ship_type_id: 29990,
    side_kind: 'hostile',
    isk: 1_000_000,
  }

  it('escapes pilot and ship names', async () => {
    const { renderKillTip } = await import('./hoverSummary')
    const html = renderKillTip(kill, null)
    expect(html).not.toContain('<img src=x')
    expect(html).not.toContain('<b>bold</b>')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).toContain('Loki &lt;b&gt;bold&lt;/b&gt;')
    // The only <img> is the ship icon built from the numeric type id.
    expect(html.match(/<img /g)).toHaveLength(1)
  })

  it('renders the flagged-death line when given', async () => {
    const { renderKillTip } = await import('./hoverSummary')
    expect(renderKillTip(kill, 'No rep broadcast')).toContain('⚠ No rep broadcast')
    expect(renderKillTip(kill, null)).not.toContain('⚠')
  })
})
