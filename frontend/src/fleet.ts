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
// both encode direction so it reads at a glance. Smoothing is a centered moving
// average per family.

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
 * Centered moving average over `win` buckets. Nulls inside the active span
 * (first→last non-null) count as 0; outside the span values stay null so the
 * curve doesn't bleed beyond real activity. `win <= 1` returns a copy.
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

  const half = Math.floor(win / 2)
  const out: (number | null)[] = new Array(n).fill(null)
  for (let i = first; i <= last; i++) {
    const lo = Math.max(first, i - half)
    const hi = Math.min(last, i + half)
    let sum = 0
    for (let j = lo; j <= hi; j++) sum += values[j] ?? 0
    out[i] = sum / (hi - lo + 1)
  }
  return out
}

export function smoothWindowBuckets(seconds: number, bucketSeconds: number, scale: number): number {
  return Math.max(1, Math.round((seconds * scale) / Math.max(1, bucketSeconds)))
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
    if (smooth) {
      values = smoothSeries(values, smoothWindowBuckets(fam.smoothSec, bucketSeconds, smoothScale))
    }
    if (fam.dir === 'in') values = values.map((v) => (v == null ? null : -v))

    const ps: PanelSeries = {
      key: fam.id,
      label: fam.label,
      stroke: fam.color,
      direction: fam.dir,
      defaultVisible: fam.defaultVisible,
      values,
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
