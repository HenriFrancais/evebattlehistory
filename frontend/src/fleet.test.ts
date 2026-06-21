import { describe, expect, it } from 'vitest'
import type { FleetTimeline } from './api'
import { smoothSeries, smoothWindowBuckets, toFleetView } from './fleet'

const emptyFleet: FleetTimeline = {
  x: [],
  series: [],
  kills: [],
  fights: [],
  bucket_seconds: 5,
  t_start: null,
  t_end: null,
  leaders: [],
}

function mk(effect_type: string, direction: string, values: (number | null)[]) {
  const metric = ['scram', 'disrupt', 'jam'].includes(effect_type) ? 'count' : 'amount'
  return { key: `${effect_type}:${direction}`, effect_type, direction, metric, values }
}

const fleetWithData: FleetTimeline = {
  x: [1000, 1005, 1010],
  series: [
    mk('damage', 'out', [100, 200, 150]),
    mk('damage', 'in', [10, 20, 30]),
    mk('rep_armor', 'in', [50, null, 25]),
    mk('rep_shield', 'in', [5, 5, 5]),
    mk('neut', 'out', [40, 40, 40]),
    mk('nos', 'out', [10, 10, 10]),
    mk('nos', 'in', [7, 7, 7]),
    mk('cap_transfer', 'in', [80, 80, 80]),
    mk('scram', 'out', [1, 1, 1]),
    mk('disrupt', 'out', [1, 1, 1]),
    mk('scram', 'in', [2, 2, 2]),
    mk('jam', 'in', [1, 1, 1]),
  ],
  kills: [
    { ts: 1005, killmail_id: 42, victim_character_id: 999, victim_character_name: 'Tengu Pilot', victim_ship_name: 'Tengu', victim_ship_type_id: 17738, side_kind: 'hostile', isk: 1_500_000_000 },
  ],
  fights: [],
  bucket_seconds: 5,
  t_start: 1000,
  t_end: 1010,
  leaders: [],
}

describe('smoothSeries', () => {
  it('win<=1 returns a copy unchanged', () => {
    expect(smoothSeries([1, 2, 3], 1)).toEqual([1, 2, 3])
  })

  it('centered average smooths a spike', () => {
    const out = smoothSeries([0, 0, 9, 0, 0], 3)
    expect(out[2]).toBeCloseTo(3)
    expect(out[1]).toBeCloseTo(3)
  })

  it('keeps nulls outside the active span', () => {
    const out = smoothSeries([null, null, 4, 4, null], 3)
    expect(out[0]).toBeNull()
    expect(out[4]).toBeNull()
    expect(out[2]).not.toBeNull()
  })
})

describe('smoothWindowBuckets', () => {
  it('derives bucket count from seconds / bucket size', () => {
    expect(smoothWindowBuckets(10, 5, 1)).toBe(2)
    expect(smoothWindowBuckets(25, 5, 1)).toBe(5)
  })
  it('scale widens the window', () => {
    expect(smoothWindowBuckets(10, 5, 3)).toBe(6)
  })
})

describe('toFleetView (families)', () => {
  it('empty fleet → no panels', () => {
    const v = toFleetView(emptyFleet)
    expect(v.panels).toHaveLength(0)
  })

  it('groups into damage / cap / ewar panels in order', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    expect(v.panels.map((p) => p.id)).toEqual(['damage', 'cap', 'ewar'])
  })

  it('aggregates rep_armor + rep_shield into one "Rep received" family', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    const damage = v.panels.find((p) => p.id === 'damage')!
    const rep = damage.series.find((s) => s.key === 'rep_in')!
    expect(rep.label).toBe('Rep received')
    // index 0: armor 50 + shield 5 = 55, mirrored (incoming) → -55
    expect(rep.values[0]).toBe(-55)
    // index 1: armor null + shield 5 = 5 → -5 (null member ignored, not whole-null)
    expect(rep.values[1]).toBe(-5)
  })

  it('aggregates neut + nos into "Neut/NOS applied"', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    const cap = v.panels.find((p) => p.id === 'cap')!
    const out = cap.series.find((s) => s.key === 'cap_out')!
    expect(out.values[0]).toBe(50) // neut 40 + nos 10, outgoing positive
  })

  it('aggregates scram + disrupt + jam into tackle families', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    const ewar = v.panels.find((p) => p.id === 'ewar')!
    const tin = ewar.series.find((s) => s.key === 'tackle_in')!
    // scram in 2 + jam in 1 = 3, mirrored → -3
    expect(tin.values[0]).toBe(-3)
    const tout = ewar.series.find((s) => s.key === 'tackle_out')!
    expect(tout.values[0]).toBe(2) // scram 1 + disrupt 1
  })

  it('mirrors incoming families below the baseline; outgoing stay positive', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    const damage = v.panels.find((p) => p.id === 'damage')!
    expect(damage.series.find((s) => s.key === 'dmg_out')!.values).toEqual([100, 200, 150])
    expect(damage.series.find((s) => s.key === 'dmg_in')!.values).toEqual([-10, -20, -30])
  })

  it('optional families (rep applied, cap given) default-hidden; rest visible', () => {
    const withApplied: FleetTimeline = {
      ...fleetWithData,
      series: [...fleetWithData.series, mk('rep_armor', 'out', [9, 9, 9]), mk('cap_transfer', 'out', [9, 9, 9])],
    }
    const all = toFleetView(withApplied, { smooth: false }).panels.flatMap((p) => p.series)
    expect(all.find((s) => s.key === 'rep_out')!.defaultVisible).toBe(false)
    expect(all.find((s) => s.key === 'capxfer_out')!.defaultVisible).toBe(false)
    expect(all.find((s) => s.key === 'dmg_out')!.defaultVisible).toBe(true)
    expect(all.find((s) => s.key === 'rep_in')!.defaultVisible).toBe(true)
  })

  it('omits families with no member data', () => {
    const onlyDamage: FleetTimeline = { ...fleetWithData, series: [mk('damage', 'out', [1, 2, 3])] }
    const v = toFleetView(onlyDamage, { smooth: false })
    expect(v.panels.map((p) => p.id)).toEqual(['damage'])
    const keys = v.panels[0].series.map((s) => s.key)
    expect(keys).toEqual(['dmg_out'])
  })

  it('passes kills through', () => {
    const v = toFleetView(fleetWithData)
    expect(v.kills).toHaveLength(1)
    expect(v.kills[0].killmail_id).toBe(42)
  })
})
