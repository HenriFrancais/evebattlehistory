// Pure functions over the per-pilot bucket timeline: range statistics for the
// stats table, and the summed series drawn when pilots are isolated. Everything
// the timeline tab shows while the user drags a range, switches stat type or ticks
// a pilot is derived here, in the browser, with no request.
import type { FleetSeriesItem, PilotTimeline } from './api'

export type StatFamily = 'damage' | 'reps' | 'cap' | 'ewar'
export type StatDirection = 'out' | 'in' | 'both'

export const STAT_FAMILIES: { id: StatFamily; label: string }[] = [
  { id: 'damage', label: 'Damage' },
  { id: 'reps', label: 'Reps' },
  { id: 'cap', label: 'Cap' },
  { id: 'ewar', label: 'EWAR' },
]

/** effect_types that make up each stat type. */
export const FAMILY_EFFECTS: Record<StatFamily, string[]> = {
  damage: ['damage'],
  reps: ['rep_armor', 'rep_shield'],
  cap: ['neut', 'nos', 'cap_transfer'],
  ewar: ['scram', 'disrupt', 'jam'],
}

const MISS = 'miss'
/** Effects measured as a number of applications, not an amount. */
const COUNT_EFFECTS = new Set(['scram', 'disrupt', 'jam', MISS])

export interface PilotStatRow {
  characterId: number
  /** Sum over the range (HP / GJ), or number of applications for EWAR. */
  total: number
  /** Busiest 5-second bucket, per second; for EWAR the bucket's raw count. */
  peak: number
  /** Total divided by the range length; null for EWAR or an empty range. */
  avgPerSec: number | null
  /** Smallest / largest single hit or cycle; null when none was recorded. */
  min: number | null
  max: number | null
  /** Hits, cycles or applications. */
  hits: number
  /** Misses (damage only); null when not applicable or not recorded. */
  misses: number | null
}

export interface StatsOpts {
  family: StatFamily
  /** effect_types to include (a subset of the family's, following the visible series). */
  effects: string[]
  direction: 'out' | 'in'
  /** Range in epoch seconds: buckets with from <= start < to are included. */
  from: number
  to: number
}

export interface Stats {
  rows: PilotStatRow[]
  /** All pilots together; its peak is the fleet's busiest bucket, not a sum of peaks. */
  totals: PilotStatRow
}

/**
 * Widen a range outward to whole buckets. Statistics are held per bucket, so this
 * is the range the numbers actually cover: the label, the table, its averages and
 * the server breakdown all use it and therefore agree. An inverted range is put
 * the right way round; the result always spans at least one bucket.
 */
export function snapRange(
  r: { from: number; to: number }, bucketSeconds: number,
): { from: number; to: number } {
  const b = Math.max(1, bucketSeconds)
  const lo = Math.min(r.from, r.to)
  const hi = Math.max(r.from, r.to)
  const from = Math.floor(lo / b) * b
  const to = Math.max(Math.ceil(hi / b) * b, from + b)
  return { from, to }
}

/** True when the timeline carries miss data at all (logs parsed since misses were kept). */
export function hasMissData(tl: PilotTimeline): boolean {
  return tl.pilots.some((p) => p.series.some((s) => s.effect_type === MISS))
}

interface Acc {
  buckets: Map<number, number>
  total: number
  hits: number
  misses: number
  min: number | null
  max: number | null
}

const newAcc = (): Acc => ({ buckets: new Map(), total: 0, hits: 0, misses: 0, min: null, max: null })

function finish(
  characterId: number, a: Acc, o: StatsOpts, bucketSeconds: number, missesKnown: boolean,
): PilotStatRow {
  const isCount = o.family === 'ewar'
  let peakBucket = 0
  for (const v of a.buckets.values()) if (v > peakBucket) peakBucket = v
  const span = o.to - o.from
  return {
    characterId,
    total: a.total,
    peak: isCount ? peakBucket : peakBucket / Math.max(1, bucketSeconds),
    avgPerSec: isCount || !(span > 0) ? null : a.total / span,
    min: isCount ? null : a.min,
    max: isCount ? null : a.max,
    hits: a.hits,
    misses: o.family === 'damage' && missesKnown ? a.misses : null,
  }
}

/** Per-pilot statistics for one stat type and direction over [from, to). */
export function computeStats(tl: PilotTimeline, o: StatsOpts): Stats {
  const wanted = new Set(o.effects)
  const missesKnown = hasMissData(tl)
  const all = newAcc()
  const rows: PilotStatRow[] = []
  for (const p of tl.pilots) {
    const a = newAcc()
    for (const s of p.series) {
      if (s.direction !== o.direction) continue
      const isMiss = s.effect_type === MISS
      if (!isMiss && !wanted.has(s.effect_type)) continue
      if (isMiss && o.family !== 'damage') continue
      for (let k = 0; k < s.idx.length; k++) {
        const i = s.idx[k]
        const t = tl.x[i]
        if (t == null || t < o.from || t >= o.to) continue
        if (isMiss) {
          a.misses += s.count[k] ?? 0
          all.misses += s.count[k] ?? 0
          continue
        }
        const v = s.sum[k] ?? 0
        const n = s.count[k] ?? 0
        for (const acc of [a, all]) {
          acc.total += v
          acc.hits += n
          acc.buckets.set(i, (acc.buckets.get(i) ?? 0) + v)
          const lo = s.min[k]
          const hi = s.max[k]
          if (lo != null && (acc.min == null || lo < acc.min)) acc.min = lo
          if (hi != null && (acc.max == null || hi > acc.max)) acc.max = hi
        }
      }
    }
    rows.push(finish(p.character_id, a, o, tl.bucket_seconds, missesKnown))
  }
  return { rows, totals: finish(0, all, o, tl.bucket_seconds, missesKnown) }
}

/** True when a row has nothing to show for the range. */
export function isIdle(r: PilotStatRow): boolean {
  return r.total === 0 && r.hits === 0 && !r.misses
}

/**
 * The selected pilots' series summed into dense arrays on the timeline's x axis —
 * the same shape the fleet timeline uses, so the chart can draw either. Misses are
 * not a chart series. Unknown ids are ignored.
 */
export function sumSeries(tl: PilotTimeline, characterIds: Set<number>): FleetSeriesItem[] {
  const out = new Map<string, FleetSeriesItem>()
  for (const p of tl.pilots) {
    if (!characterIds.has(p.character_id)) continue
    for (const s of p.series) {
      if (s.effect_type === MISS) continue
      const key = `${s.effect_type}:${s.direction}`
      let item = out.get(key)
      if (!item) {
        item = {
          key,
          effect_type: s.effect_type,
          direction: s.direction,
          metric: COUNT_EFFECTS.has(s.effect_type) ? 'count' : 'amount',
          values: new Array<number | null>(tl.x.length).fill(null),
        }
        out.set(key, item)
      }
      for (let k = 0; k < s.idx.length; k++) {
        const i = s.idx[k]
        if (i < 0 || i >= tl.x.length) continue
        item.values[i] = (item.values[i] ?? 0) + (s.sum[k] ?? 0)
      }
    }
  }
  return [...out.values()]
}

// Colours for pilots compared on the chart, in fixed slot order. Checked for
// colour-blind separation and contrast against the dark panel.
const COMPARE_COLORS = [
  '#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767',
]

export interface PilotStyle {
  color: string
  /** Set from the ninth pilot on: the colours repeat with a dashed line. */
  dash?: number[]
}

export function pilotStyle(slot: number): PilotStyle {
  const color = COMPARE_COLORS[slot % COMPARE_COLORS.length]
  return slot < COMPARE_COLORS.length ? { color } : { color, dash: [6, 4] }
}

/**
 * Give every pilot in `ids` a colour slot, keeping the slot of any pilot already in
 * `prev` so that ticking or unticking one pilot never repaints the others. A new
 * pilot takes the lowest free slot.
 */
export function assignSlots(prev: Map<number, number>, ids: Iterable<number>): Map<number, number> {
  const next = new Map<number, number>()
  const fresh: number[] = []
  for (const id of ids) {
    const slot = prev.get(id)
    if (slot == null) fresh.push(id)
    else next.set(id, slot)
  }
  const used = new Set(next.values())
  let slot = 0
  for (const id of fresh) {
    while (used.has(slot)) slot++
    next.set(id, slot)
    used.add(slot)
  }
  return next
}
