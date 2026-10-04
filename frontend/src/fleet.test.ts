import { describe, expect, it } from 'vitest'
import type { FleetTimeline } from './api'
import {
  clipToWindow, cycleAwareWindow, detectCycle, seriesEffects, smoothSeries, smoothWindowBuckets, toFleetView,
} from './fleet'

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

  it('spreads a spike with the most weight at the centre, keeping its total', () => {
    const out = smoothSeries([0, 0, 0, 0, 0, 9, 0, 0, 0, 0, 0], 3) as number[]
    expect(out.slice(2, 9).map((v) => Math.round(v))).toEqual([0, 1, 2, 3, 2, 1, 0]) // weights 1,2,3,2,1
    expect(out.reduce((a, b) => a + b, 0)).toBeCloseTo(9)
  })

  it('keeps nulls outside the active span', () => {
    const out = smoothSeries([null, null, 4, 4, null], 3)
    expect(out[0]).toBeNull()
    expect(out[4]).toBeNull()
    expect(out[2]).not.toBeNull()
  })
})

// Active in every bucket, amounts varying with no period (a fixed pseudo-random walk).
function noisy(len = 60): number[] {
  let seed = 7
  return Array.from({ length: len }, () => {
    seed = (seed * 1103515245 + 12345) % 2147483648
    return 100 + (seed % 23)
  })
}

describe('detectCycle', () => {
  const every = (n: number, len = 60) => Array.from({ length: len }, (_, i) => (i % n === 0 ? 100 : null))

  it('finds the gap between bursts of a slow weapon', () => {
    expect(detectCycle(every(3))).toBe(3)
    expect(detectCycle(every(5))).toBe(5)
  })

  it('is 1 for a series active in every bucket with no repeat', () => {
    const steady = noisy()
    expect(detectCycle(steady)).toBe(1)
  })

  it('finds a repeat in the amounts even when no bucket is empty', () => {
    const volleys = Array.from({ length: 60 }, (_, i) => (i % 4 === 0 ? 900 : 50))
    expect(detectCycle(volleys)).toBe(4)
  })

  it('ignores a long lull between engagements and one huge hit', () => {
    const v = [...every(3, 30), ...new Array(40).fill(null), ...every(3, 30)]
    v[3] = 50_000
    expect(detectCycle(v)).toBe(3)
  })

  it('does not take a gap longer than 30 seconds for a cycle', () => {
    expect(detectCycle(every(8))).toBe(1)
  })

  it('is 1 with too little to go on', () => {
    expect(detectCycle([null, 5, null, null, 7])).toBe(1)
    expect(detectCycle([])).toBe(1)
  })
})

describe('cycleAwareWindow', () => {
  const every = (n: number) => Array.from({ length: 60 }, (_, i) => (i % n === 0 ? 100 : null))

  it('keeps the family default for a dense series', () => {
    const steady = noisy()
    expect(cycleAwareWindow(steady, 25, 5, 1)).toBe(5)
  })

  it('widens to 2.5 cycles for a sparse series, and follows the scale', () => {
    expect(cycleAwareWindow(every(5), 10, 5, 1)).toBe(13) // 5 buckets * 2.5
    expect(cycleAwareWindow(every(5), 10, 5, 2)).toBe(25)
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

  it('groups into damage / reps / cap / ewar panels in order', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    expect(v.panels.map((p) => p.id)).toEqual(['damage', 'reps', 'cap', 'ewar'])
  })

  it('aggregates rep_armor + rep_shield into one "Rep received" family', () => {
    const v = toFleetView(fleetWithData, { smooth: false })
    const reps = v.panels.find((p) => p.id === 'reps')!
    const rep = reps.series.find((s) => s.key === 'rep_in')!
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

  it('every family is visible by default now each stat type has its own panel', () => {
    const withApplied: FleetTimeline = {
      ...fleetWithData,
      series: [...fleetWithData.series, mk('rep_armor', 'out', [9, 9, 9]), mk('cap_transfer', 'out', [9, 9, 9])],
    }
    const all = toFleetView(withApplied, { smooth: false }).panels.flatMap((p) => p.series)
    expect(all.every((s) => s.defaultVisible)).toBe(true)
    expect(seriesEffects('rep_out')).toEqual({ direction: 'out', effects: ['rep_armor', 'rep_shield'] })
    expect(seriesEffects('nope')).toBeNull()
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

  it('reports how many seconds each smoothed series spans, and nothing when not smoothed', () => {
    const sparse = Array.from({ length: 60 }, (_, i) => (i % 5 === 0 ? 100 : null))
    const fleet: FleetTimeline = {
      ...emptyFleet, x: sparse.map((_, i) => i * 5), series: [mk('neut', 'out', sparse)],
    }
    const on = toFleetView(fleet).panels[0].series[0]
    expect(on.smoothSeconds).toBe(65) // 13 buckets of 5 s
    // The line no longer drops to zero between cycles.
    expect(Math.min(...(on.values.slice(10, 50) as number[]))).toBeGreaterThan(0)
    expect(toFleetView(fleet, { smooth: false }).panels[0].series[0].smoothSeconds).toBeUndefined()
  })

  it('keeps the fixed window for tackle / EWAR, which is a count and not a cycle', () => {
    const sparse = Array.from({ length: 60 }, (_, i) => (i % 5 === 0 ? 1 : null))
    const fleet: FleetTimeline = {
      ...emptyFleet, x: sparse.map((_, i) => i * 5), series: [mk('scram', 'out', sparse)],
    }
    expect(toFleetView(fleet).panels[0].series[0].smoothSeconds).toBe(25)
  })

  it('passes kills through', () => {
    const v = toFleetView(fleetWithData)
    expect(v.kills).toHaveLength(1)
    expect(v.kills[0].killmail_id).toBe(42)
  })
})

describe('table effects follow the chart families', () => {
  it('counts outgoing jams as tackle applied, as incoming jams are tackle received', () => {
    expect(seriesEffects('tackle_out')!.effects).toContain('jam')
    expect(seriesEffects('tackle_in')!.effects).toContain('jam')
  })
})

describe('clipToWindow', () => {
  const x = [90, 95, 100, 105, 110, 115]
  const col = [1, 2, 3, 4, 5, 6]

  it('keeps buckets inside the window and remembers where each came from', () => {
    const c = clipToWindow(x, [col], 100, 110)
    expect(c.xs).toEqual([100, 105, 110])
    expect(c.cols).toEqual([[3, 4, 5]])
    expect(c.sourceIdx).toEqual([2, 3, 4])
  })

  it('pads to the window edges with points that map to no source bucket', () => {
    const c = clipToWindow(x, [col], 97, 112)
    expect(c.xs).toEqual([97, 100, 105, 110, 112])
    expect(c.cols).toEqual([[null, 3, 4, 5, null]])
    expect(c.sourceIdx).toEqual([null, 2, 3, 4, null])
  })

  it('handles a window with no buckets in it', () => {
    const c = clipToWindow(x, [col], 200, 300)
    expect(c.xs).toEqual([200, 300])
    expect(c.sourceIdx).toEqual([null, null])
  })
})
