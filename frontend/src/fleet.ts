// Pure transforms: FleetTimeline API response → stacked-panel view model.
// No uPlot import — unit-tested in jsdom without canvas.
//
// Taxonomy: the backend returns one raw series per (effect_type, direction).
// We aggregate those into a small set of semantically-named "families" that
// match how an FC thinks about a fight — each a distinct colour:
//
//   Damage (HP/s)           Damage applied · Damage received
//   Reps (HP/s)             Rep applied · Rep received
//   Cap warfare (GJ/s)      Neut/NOS applied · received, Cap applied · received
//   Tackle / EWAR (apps)    Tackle applied · Tackle received
//
// One panel is one stat type of the timeline chart. Outgoing families draw ABOVE
// a zero baseline, incoming families mirrored BELOW (negated). Colour AND position
// both encode direction so it reads at a glance. Smoothing is a weighted moving
// average whose window follows each series' own cycle length, so slow weapons
// (torpedoes, heavy neutralizers) are not drawn as a saw-tooth.

import type { FleetTimeline, KillEvent } from './api'

export type PanelId = 'damage' | 'reps' | 'cap' | 'ewar'

interface FamilyDef {
  id: string
  label: string
  panel: PanelId
  dir: 'out' | 'in'
  color: string
  /** (effect_type, direction) members summed into this family. */
  members: [string, string][]
  smoothSec: number
  defaultVisible: boolean
}

// Consistent verbs: outgoing effects are "applied", incoming effects "received".
// Order within a panel = legend + draw order.
const FAMILIES: FamilyDef[] = [
  // Damage
  { id: 'dmg_out', label: 'Damage applied', panel: 'damage', dir: 'out', color: '#ff7043',
    members: [['damage', 'out']], smoothSec: 10, defaultVisible: true },
  { id: 'dmg_in', label: 'Damage received', panel: 'damage', dir: 'in', color: '#e53935',
    members: [['damage', 'in']], smoothSec: 10, defaultVisible: true },
  // Reps
  { id: 'rep_out', label: 'Rep applied', panel: 'reps', dir: 'out', color: '#26a69a',
    members: [['rep_armor', 'out'], ['rep_shield', 'out']], smoothSec: 20, defaultVisible: true },
  { id: 'rep_in', label: 'Rep received', panel: 'reps', dir: 'in', color: '#66bb6a',
    members: [['rep_armor', 'in'], ['rep_shield', 'in']], smoothSec: 20, defaultVisible: true },
  // Cap warfare
  { id: 'cap_out', label: 'Neut/NOS applied', panel: 'cap', dir: 'out', color: '#ab47bc',
    members: [['neut', 'out'], ['nos', 'out']], smoothSec: 25, defaultVisible: true },
  { id: 'cap_in', label: 'Neut/NOS received', panel: 'cap', dir: 'in', color: '#ec407a',
    members: [['neut', 'in'], ['nos', 'in']], smoothSec: 25, defaultVisible: true },
  { id: 'capxfer_in', label: 'Cap received', panel: 'cap', dir: 'in', color: '#26c6da',
    members: [['cap_transfer', 'in']], smoothSec: 25, defaultVisible: true },
  { id: 'capxfer_out', label: 'Cap applied', panel: 'cap', dir: 'out', color: '#7e57c2',
    members: [['cap_transfer', 'out']], smoothSec: 25, defaultVisible: true },
  // Tackle / EWAR
  { id: 'tackle_out', label: 'Tackle applied', panel: 'ewar', dir: 'out', color: '#42a5f5',
    members: [['scram', 'out'], ['disrupt', 'out'], ['jam', 'out']], smoothSec: 25, defaultVisible: true },
  { id: 'tackle_in', label: 'Tackle received', panel: 'ewar', dir: 'in', color: '#ffca28',
    members: [['scram', 'in'], ['disrupt', 'in'], ['jam', 'in']], smoothSec: 25, defaultVisible: true },
]

const PANEL_META: Record<PanelId, { title: string; unit: string; order: number }> = {
  damage: { title: 'Damage', unit: 'HP', order: 0 },
  reps: { title: 'Remote reps', unit: 'HP', order: 1 },
  cap: { title: 'Cap warfare', unit: 'GJ', order: 2 },
  ewar: { title: 'Tackle / EWAR', unit: '#', order: 3 },
}

/** Chart series (family ids) of one stat type, in legend order. */
export function panelSeriesKeys(panel: PanelId): string[] {
  return FAMILIES.filter((f) => f.panel === panel).map((f) => f.id)
}

/** The effect_types a chart series (family id) sums, for one direction. */
export function seriesEffects(key: string): { direction: 'out' | 'in'; effects: string[] } | null {
  const fam = FAMILIES.find((f) => f.id === key)
  return fam ? { direction: fam.dir, effects: fam.members.map(([e]) => e) } : null
}

export interface PanelSeries {
  key: string // family id
  label: string
  stroke: string
  direction: 'out' | 'in'
  defaultVisible: boolean
  /** Mirrored, smoothed values: out positive, in negative. Null where no data. */
  values: (number | null)[]
  /** Seconds the smoothing spans either side of a point; absent when not smoothed. */
  smoothSeconds?: number
  /** Line dash pattern; solid when absent. */
  dash?: number[]
  /** Draw the line only, without the area under it (many overlapping lines). */
  noFill?: boolean
}

export interface FleetPanel {
  id: PanelId
  title: string
  unit: string
  series: PanelSeries[]
}

export interface FleetView {
  x: number[]
  panels: FleetPanel[]
  kills: KillEvent[]
}

/**
 * Weighted moving average: a bucket `win` away or further has no weight, the centre
 * has the most (a triangle), so a hit fades in and out of the line instead of
 * stepping. Nulls inside the active span (first→last non-null) count as 0; outside
 * the span values stay null so the curve doesn't bleed beyond real activity.
 * `win <= 1` returns a copy.
 */
export function smoothSeries(values: (number | null)[], win: number): (number | null)[] {
  const n = values.length
  if (win <= 1 || n === 0) return values.slice()

  let first = -1
  let last = -1
  for (let i = 0; i < n; i++) {
    if (values[i] != null) {
      if (first === -1) first = i
      last = i
    }
  }
  if (first === -1) return values.slice()

  const out: (number | null)[] = new Array(n).fill(null)
  for (let i = first; i <= last; i++) {
    const lo = Math.max(first, i - win + 1)
    const hi = Math.min(last, i + win - 1)
    let sum = 0
    let weights = 0
    for (let j = lo; j <= hi; j++) {
      const w = win - Math.abs(j - i)
      sum += (values[j] ?? 0) * w
      weights += w
    }
    out[i] = sum / weights
  }
  return out
}

/**
 * Cycles longer than this many buckets are not looked for: the slowest modules
 * (heavy neutralizers, large artillery) cycle in under 30 seconds, and a longer gap
 * is a lull in the fight.
 */
const MAX_CYCLE_BUCKETS = 6
/** How many of a series' own cycles the smoothing covers. */
const SMOOTH_CYCLES = 2.5

/**
 * The series' repeat interval in buckets: slow weapons (torpedoes, heavy
 * neutralizers) log one burst per cycle with nothing in between, and a window
 * shorter than a couple of cycles draws that as a saw-tooth. Takes the larger of
 * the typical gap between active buckets and the strongest repeat in the values
 * (clipped at their 90th percentile so one huge hit does not decide it). 1 when
 * the series is active in most buckets and has no clear repeat.
 */
export function detectCycle(values: (number | null)[]): number {
  const active: number[] = []
  values.forEach((v, i) => { if (v) active.push(i) })
  if (active.length < 3) return 1

  const gaps: number[] = []
  for (let k = 1; k < active.length; k++) {
    const g = active[k] - active[k - 1]
    if (g <= MAX_CYCLE_BUCKETS) gaps.push(g) // longer = a lull in the fight, not a cycle
  }
  gaps.sort((a, b) => a - b)
  const typicalGap = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1

  const first = active[0]
  const last = active[active.length - 1]
  const amounts = active.map((i) => values[i] as number).sort((a, b) => a - b)
  const p90 = amounts[Math.floor(amounts.length * 0.9)]
  const span: number[] = []
  for (let i = first; i <= last; i++) span.push(Math.min(values[i] ?? 0, p90))
  const mean = span.reduce((a, b) => a + b, 0) / span.length
  const z = span.map((v) => v - mean)
  const variance = z.reduce((a, b) => a + b * b, 0)
  let repeat = 1
  let best = 0.2 * variance // a weaker repeat than this is noise
  for (let lag = 2; lag <= MAX_CYCLE_BUCKETS; lag++) {
    let ac = 0
    for (let i = lag; i < z.length; i++) ac += z[i] * z[i - lag]
    if (ac > best) {
      best = ac
      repeat = lag
    }
  }
  return Math.max(typicalGap, repeat)
}

export function smoothWindowBuckets(seconds: number, bucketSeconds: number, scale: number): number {
  return Math.max(1, Math.round((seconds * scale) / Math.max(1, bucketSeconds)))
}

/**
 * Smoothing window for one series, in buckets: the family's default, or
 * SMOOTH_CYCLES of the series' own cycles when that is longer, times the user's scale.
 */
export function cycleAwareWindow(
  values: (number | null)[], seconds: number, bucketSeconds: number, scale: number,
): number {
  const b = Math.max(1, bucketSeconds)
  return Math.max(1, Math.round(Math.max(seconds / b, detectCycle(values) * SMOOTH_CYCLES) * scale))
}

/** Sum member arrays element-wise; null where ALL members are null at an index. */
function sumMembers(arrays: (number | null)[][], len: number): (number | null)[] {
  const out: (number | null)[] = new Array(len).fill(null)
  for (let i = 0; i < len; i++) {
    let acc: number | null = null
    for (const a of arrays) {
      const v = a[i]
      if (v != null) acc = (acc ?? 0) + v
    }
    out[i] = acc
  }
  return out
}

export interface Clipped {
  xs: number[]
  cols: (number | null)[][]
  /** For each chart point, its index in the source x axis; null for a padding point. */
  sourceIdx: (number | null)[]
}

/**
 * Clip series to [from, to] and pin the axis to both edges with null padding points.
 * `sourceIdx` lets a cursor position on the clipped chart be read back against data
 * indexed by the ORIGINAL x axis (the per-bucket leaders of the hover summary).
 */
export function clipToWindow(
  x: number[], cols: (number | null)[][], from: number, to: number,
): Clipped {
  const keep: number[] = []
  for (let i = 0; i < x.length; i++) if (x[i] >= from && x[i] <= to) keep.push(i)
  let xs = keep.map((i) => x[i])
  let out = cols.map((c) => keep.map((i) => c[i] ?? null))
  let sourceIdx: (number | null)[] = keep.slice()
  if (!xs.length || xs[0] > from) {
    xs = [from, ...xs]
    out = out.map((c) => [null, ...c])
    sourceIdx = [null, ...sourceIdx]
  }
  if (xs[xs.length - 1] < to) {
    xs = [...xs, to]
    out = out.map((c) => [...c, null])
    sourceIdx = [...sourceIdx, null]
  }
  return { xs, cols: out, sourceIdx }
}

export interface ToFleetViewOpts {
  smooth?: boolean
  /** Multiplier on the per-family default window (1 = default). */
  smoothScale?: number
  bucketSeconds?: number
}

export function toFleetView(fleet: FleetTimeline, opts: ToFleetViewOpts = {}): FleetView {
  const { smooth = true, smoothScale = 1, bucketSeconds = fleet.bucket_seconds || 5 } = opts
  const len = fleet.x.length

  // Index raw series by "effect:direction".
  const raw = new Map<string, (number | null)[]>()
  for (const s of fleet.series) raw.set(`${s.effect_type}:${s.direction}`, s.values)

  const byPanel = new Map<PanelId, PanelSeries[]>()
  for (const fam of FAMILIES) {
    const memberArrays = fam.members
      .map(([e, d]) => raw.get(`${e}:${d}`))
      .filter((a): a is (number | null)[] => a != null)
    if (memberArrays.length === 0) continue // family absent from this BR

    let values = sumMembers(memberArrays, len)
    let smoothSeconds: number | undefined
    if (smooth) {
      // Tackle / EWAR is a count of applications, not a cycling amount: fixed window.
      const win = fam.panel === 'ewar'
        ? smoothWindowBuckets(fam.smoothSec, bucketSeconds, smoothScale)
        : cycleAwareWindow(values, fam.smoothSec, bucketSeconds, smoothScale)
      values = smoothSeries(values, win)
      if (win > 1) smoothSeconds = win * bucketSeconds
    }
    if (fam.dir === 'in') values = values.map((v) => (v == null ? null : -v))

    const ps: PanelSeries = {
      key: fam.id,
      label: fam.label,
      stroke: fam.color,
      direction: fam.dir,
      defaultVisible: fam.defaultVisible,
      values,
      smoothSeconds,
    }
    const arr = byPanel.get(fam.panel)
    if (arr) arr.push(ps)
    else byPanel.set(fam.panel, [ps])
  }

  const panels: FleetPanel[] = [...byPanel.entries()]
    .map(([id, series]) => ({ id, title: PANEL_META[id].title, unit: PANEL_META[id].unit, series }))
    .sort((a, b) => PANEL_META[a.id].order - PANEL_META[b.id].order)

  return { x: fleet.x, panels, kills: fleet.kills }
}
