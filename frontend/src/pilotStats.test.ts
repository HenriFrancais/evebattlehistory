import type { PilotSeries, PilotTimeline } from './api'
import { computeStats, hasMissData, isIdle, snapRange, sumSeries } from './pilotStats'

// Four 5-second buckets starting at t=100.
const X = [100, 105, 110, 115]

function series(
  effect_type: string, direction: string,
  cells: [idx: number, sum: number, count: number, min: number | null, max: number | null][],
): PilotSeries {
  return {
    effect_type, direction,
    idx: cells.map((c) => c[0]), sum: cells.map((c) => c[1]), count: cells.map((c) => c[2]),
    min: cells.map((c) => c[3]), max: cells.map((c) => c[4]),
  }
}

function timeline(extra: Partial<PilotTimeline> = {}): PilotTimeline {
  return {
    x: X, bucket_seconds: 5, scope: 'all',
    pilots: [
      {
        character_id: 1, character_name: 'Ada', ship_type_id: null, ship_name: 'Loki',
        side_kind: 'friendly', is_self: false,
        series: [
          series('damage', 'out', [[0, 100, 2, 40, 60], [1, 300, 3, 50, 150], [3, 50, 1, 50, 50]]),
          series('damage', 'in', [[1, 900, 9, 10, 400]]),
          series('miss', 'out', [[0, 2, 2, null, null], [3, 1, 1, null, null]]),
          series('scram', 'out', [[1, 3, 3, null, null], [2, 1, 1, null, null]]),
          series('rep_armor', 'out', [[2, 800, 2, 400, 400]]),
        ],
      },
      {
        character_id: 2, character_name: 'Bo', ship_type_id: null, ship_name: 'Guardian',
        side_kind: 'friendly', is_self: false,
        series: [series('damage', 'out', [[1, 200, 2, 80, 120]])],
      },
    ],
    ...extra,
  }
}

const dmgOut = { family: 'damage' as const, effects: ['damage'], direction: 'out' as const }
const whole = { from: 100, to: 120 }

describe('computeStats', () => {
  it('totals, hits and misses over the whole window', () => {
    const { rows } = computeStats(timeline(), { ...dmgOut, ...whole })
    expect(rows[0]).toMatchObject({ characterId: 1, total: 450, hits: 6, misses: 3 })
    expect(rows[1]).toMatchObject({ characterId: 2, total: 200, hits: 2, misses: 0 })
  })

  it('peak is the busiest bucket per second; average is total over the range length', () => {
    const { rows } = computeStats(timeline(), { ...dmgOut, ...whole })
    expect(rows[0].peak).toBe(60) // 300 in one 5 s bucket
    expect(rows[0].avgPerSec).toBe(22.5) // 450 over 20 s
  })

  it('min and max are the smallest and largest single hit in range', () => {
    const { rows } = computeStats(timeline(), { ...dmgOut, ...whole })
    expect([rows[0].min, rows[0].max]).toEqual([40, 150])
  })

  it('includes a bucket only when its start lies in [from, to)', () => {
    // 105 <= t < 115 keeps buckets 105 and 110 only.
    const { rows } = computeStats(timeline(), { ...dmgOut, from: 105, to: 115 })
    expect(rows[0]).toMatchObject({ total: 300, hits: 3, misses: 0, min: 50, max: 150 })
    expect(rows[0].avgPerSec).toBe(30)
  })

  it('a range cutting through a bucket leaves that bucket out, never a fraction of it', () => {
    const { rows } = computeStats(timeline(), { ...dmgOut, from: 101, to: 104 })
    expect(rows[0]).toMatchObject({ total: 0, hits: 0, peak: 0, min: null, max: null })
    expect(isIdle(rows[0])).toBe(true)
  })

  it('an empty or inverted range yields zeros and a null average, never NaN or Infinity', () => {
    for (const range of [{ from: 110, to: 110 }, { from: 115, to: 100 }]) {
      const { rows, totals } = computeStats(timeline(), { ...dmgOut, ...range })
      for (const r of [...rows, totals]) {
        expect(r.total).toBe(0)
        expect(r.peak).toBe(0)
        expect(r.avgPerSec).toBeNull()
      }
    }
  })

  it('follows the direction', () => {
    const { rows } = computeStats(timeline(), { ...dmgOut, direction: 'in', ...whole })
    expect(rows[0]).toMatchObject({ total: 900, hits: 9, min: 10, max: 400, misses: 0 })
    expect(rows[1].total).toBe(0)
  })

  it('sums only the effects asked for', () => {
    const reps = { family: 'reps' as const, direction: 'out' as const, ...whole }
    expect(computeStats(timeline(), { ...reps, effects: ['rep_armor', 'rep_shield'] }).rows[0].total).toBe(800)
    expect(computeStats(timeline(), { ...reps, effects: ['rep_shield'] }).rows[0].total).toBe(0)
  })

  it('reps and cap have no misses', () => {
    const { rows } = computeStats(timeline(), {
      family: 'reps', effects: ['rep_armor'], direction: 'out', ...whole,
    })
    expect(rows[0].misses).toBeNull()
    expect(rows[0].hits).toBe(2)
  })

  it('EWAR counts applications: peak is a raw bucket count and amount columns are null', () => {
    const { rows } = computeStats(timeline(), {
      family: 'ewar', effects: ['scram', 'disrupt', 'jam'], direction: 'out', ...whole,
    })
    expect(rows[0]).toEqual({
      characterId: 1, total: 4, peak: 3, avgPerSec: null, min: null, max: null, hits: 4, misses: null,
    })
  })

  it('null min/max stays null (rendered blank), never 0', () => {
    const tl = timeline()
    tl.pilots[1].series = [series('damage', 'out', [[1, 200, 2, null, null]])]
    const { rows } = computeStats(tl, { ...dmgOut, ...whole })
    expect([rows[1].min, rows[1].max]).toEqual([null, null])
    expect(rows[1].total).toBe(200)
  })

  it('misses are null for everyone when the timeline has no miss data at all', () => {
    const tl = timeline()
    tl.pilots[0].series = tl.pilots[0].series.filter((s) => s.effect_type !== 'miss')
    expect(hasMissData(tl)).toBe(false)
    const { rows, totals } = computeStats(tl, { ...dmgOut, ...whole })
    expect(rows.map((r) => r.misses)).toEqual([null, null])
    expect(totals.misses).toBeNull()
  })

  it('totals sum the pilots, with the peak taken from the combined busiest bucket', () => {
    const { totals } = computeStats(timeline(), { ...dmgOut, ...whole })
    expect(totals).toMatchObject({ total: 650, hits: 8, misses: 3, min: 40, max: 150 })
    expect(totals.peak).toBe(100) // bucket 105: 300 + 200 over 5 s
    expect(totals.avgPerSec).toBe(32.5)
  })

  it('handles a timeline with no pilots', () => {
    const { rows, totals } = computeStats(timeline({ pilots: [] }), { ...dmgOut, ...whole })
    expect(rows).toEqual([])
    expect(totals.total).toBe(0)
  })
})

describe('snapRange', () => {
  it('widens a range outward to whole buckets', () => {
    expect(snapRange({ from: 1002.4, to: 1012.4 }, 5)).toEqual({ from: 1000, to: 1015 })
  })

  it('leaves an already aligned range alone', () => {
    expect(snapRange({ from: 1000, to: 1010 }, 5)).toEqual({ from: 1000, to: 1010 })
  })

  it('a range inside one bucket becomes that bucket, so its activity is counted', () => {
    expect(snapRange({ from: 106, to: 107 }, 5)).toEqual({ from: 105, to: 110 })
    const r = snapRange({ from: 106, to: 107 }, 5)
    const { rows } = computeStats(timeline(), { ...dmgOut, ...r })
    // 300 in the 105 bucket: the average can no longer exceed the peak.
    expect(rows[0]).toMatchObject({ total: 300, peak: 60, avgPerSec: 60 })
  })

  it('puts an inverted range the right way round and never returns an empty one', () => {
    expect(snapRange({ from: 1012, to: 1003 }, 5)).toEqual({ from: 1000, to: 1015 })
    expect(snapRange({ from: 1005, to: 1005 }, 5)).toEqual({ from: 1005, to: 1010 })
  })
})

describe('sumSeries', () => {
  it('sums the selected pilots into dense arrays on the x axis', () => {
    const out = sumSeries(timeline(), new Set([1, 2]))
    const dmg = out.find((s) => s.key === 'damage:out')!
    expect(dmg.values).toEqual([100, 500, null, 50])
    expect(dmg.metric).toBe('amount')
  })

  it('keeps one pilot apart from the rest', () => {
    const dmg = sumSeries(timeline(), new Set([2])).find((s) => s.key === 'damage:out')!
    expect(dmg.values).toEqual([null, 200, null, null])
  })

  it('marks EWAR as a count and leaves misses out', () => {
    const out = sumSeries(timeline(), new Set([1]))
    expect(out.find((s) => s.key === 'scram:out')!.metric).toBe('count')
    expect(out.some((s) => s.effect_type === 'miss')).toBe(false)
  })

  it('ignores ids that are not in the timeline', () => {
    expect(sumSeries(timeline(), new Set([99]))).toEqual([])
  })
})
