// Pure transforms for the fleet-broadcast analytics view.
// No uPlot/DOM import — unit-tested in jsdom.

import type { BroadcastMetrics, BroadcastRawItem } from './api'

export interface Histogram {
  /** Left edge (seconds) of each bin. */
  edges: number[]
  /** Count in each bin; counts.length === edges.length. */
  counts: number[]
  /** Values at or above the last edge fall into the final (overflow) bin. */
  binSec: number
  maxSec: number
  total: number
}

/**
 * Bin a list of second-deltas into a histogram for the distribution charts.
 * Null deltas (never fired / never repped) are skipped and NOT counted.
 * Negative deltas (e.g. an already-primaried target) are clamped into the first
 * bin so they remain visible rather than dropped.
 */
export function toHistogram(
  deltas: (number | null)[],
  binSec = 5,
  maxSec = 60,
): Histogram {
  const nBins = Math.max(1, Math.ceil(maxSec / binSec))
  const edges: number[] = []
  for (let i = 0; i < nBins; i++) edges.push(i * binSec)
  const counts = new Array(nBins).fill(0)
  let total = 0
  for (const d of deltas) {
    if (d === null || d === undefined || Number.isNaN(d)) continue
    total++
    const clamped = d < 0 ? 0 : d
    let idx = Math.floor(clamped / binSec)
    if (idx >= nBins) idx = nBins - 1 // overflow into the final bin
    counts[idx]++
  }
  return { edges, counts, binSec, maxSec, total }
}

/**
 * Percentile rank of `value` within `population` where LOWER is better (times).
 * Returns the % of the population the value beats (is faster than), 0–100.
 * null when the population is empty.
 */
export function percentileFaster(value: number, population: number[]): number | null {
  if (population.length === 0) return null
  const beaten = population.filter((v) => v > value).length
  return Math.round((beaten / population.length) * 100)
}

/** Percentile rank where HIGHER is better (e.g. broadcast lead time / warning). */
export function percentileHigher(value: number, population: number[]): number | null {
  if (population.length === 0) return null
  const beaten = population.filter((v) => v < value).length
  return Math.round((beaten / population.length) * 100)
}

export interface BroadcastMarker {
  ts: number // epoch ms, for plotting on a time axis
  kind: string
  label: string
}

const KIND_LABEL: Record<string, string> = {
  target: 'Target',
  needs_shield: 'Shield',
  needs_armor: 'Armor',
  needs_capacitor: 'Cap',
  repair: 'Repair',
}

/**
 * Convert raw broadcasts into timeline markers, keeping only the enabled kinds.
 * `enabledKinds` is a set of broadcast `kind` strings; an empty set yields none.
 */
export function toBroadcastMarkers(
  raw: BroadcastRawItem[],
  enabledKinds: Set<string>,
): BroadcastMarker[] {
  const out: BroadcastMarker[] = []
  for (const b of raw) {
    if (!enabledKinds.has(b.kind)) continue
    out.push({
      ts: Date.parse(b.ts),
      kind: b.kind,
      label: `${KIND_LABEL[b.kind] ?? b.kind}: ${b.subject_name}`,
    })
  }
  return out
}

export interface QualitySummary {
  targetCompliancePct: number | null // % of target calls fired on within window
  falseBroadcastPct: number | null // % of judged rep broadcasts with no incoming damage
  deathsFlaggedPct: number | null // % of deaths with a late/missing/unanswered broadcast
  unresolvedReps: number
}

function pct(n: number, d: number): number | null {
  return d > 0 ? Math.round((n / d) * 1000) / 10 : null
}

/** Derive the headline rates from a metrics payload (rounded to 0.1%). */
export function summarizeQuality(m: BroadcastMetrics): QualitySummary {
  const q = m.quality
  return {
    targetCompliancePct:
      m.summary.compliance_rate === null
        ? null
        : Math.round(m.summary.compliance_rate * 1000) / 10,
    falseBroadcastPct:
      m.summary.false_broadcast_rate === null
        ? null
        : Math.round(m.summary.false_broadcast_rate * 1000) / 10,
    deathsFlaggedPct: pct(m.summary.deaths_flagged, m.summary.deaths_total),
    unresolvedReps: q.unresolved_rep_subjects,
  }
}

/** Death classifications that constitute the "died due to late broadcast" marker. */
export const FLAGGED_DEATH_CLASSES = new Set([
  'no_broadcast',
  'late_broadcast',
  'unanswered',
])

/** character_id -> flagged death classification, for annotating fleet/timeline views. */
export function flaggedDeathsByChar(m: BroadcastMetrics): Map<number, string> {
  const out = new Map<number, string>()
  for (const d of m.deaths) {
    if (FLAGGED_DEATH_CLASSES.has(d.classification)) {
      out.set(d.character_id, d.classification)
    }
  }
  return out
}
