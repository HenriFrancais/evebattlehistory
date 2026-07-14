import { describe, expect, it } from 'vitest'

import type { BroadcastMetrics, BroadcastRawItem } from './api'
import {
  flaggedDeathsByChar,
  percentileFaster,
  percentileHigher,
  summarizeQuality,
  toBroadcastMarkers,
  toHistogram,
} from './broadcasts'

describe('percentileFaster', () => {
  it('returns the % of the population a lower value beats', () => {
    // 5 beats 6, 8, 10 (3 of 4) = 75%
    expect(percentileFaster(5, [4, 6, 8, 10])).toBe(75)
  })
  it('is null for an empty population', () => {
    expect(percentileFaster(5, [])).toBeNull()
  })
})

describe('percentileHigher', () => {
  it('returns the % of the population a higher value beats', () => {
    // 9 beats 4, 6, 8 (3 of 4) = 75%
    expect(percentileHigher(9, [4, 6, 8, 10])).toBe(75)
  })
  it('is null for an empty population', () => {
    expect(percentileHigher(9, [])).toBeNull()
  })
})

describe('toHistogram', () => {
  it('bins values and skips nulls', () => {
    const h = toHistogram([0, 3, 6, 12, null, 7], 5, 60)
    expect(h.total).toBe(5) // null skipped
    expect(h.counts[0]).toBe(2) // 0, 3
    expect(h.counts[1]).toBe(2) // 6, 7 (bin 5-10)
    expect(h.counts[2]).toBe(1) // 12 (bin 10-15)
  })

  it('clamps negatives into the first bin', () => {
    const h = toHistogram([-15, -1, 2], 5, 30)
    expect(h.counts[0]).toBe(3)
    expect(h.total).toBe(3)
  })

  it('overflows values >= maxSec into the final bin', () => {
    const h = toHistogram([100, 200], 5, 30)
    const last = h.counts.length - 1
    expect(h.counts[last]).toBe(2)
  })

  it('produces edges aligned to binSec', () => {
    const h = toHistogram([], 10, 50)
    expect(h.edges).toEqual([0, 10, 20, 30, 40])
    expect(h.counts.every((c) => c === 0)).toBe(true)
  })
})

describe('toBroadcastMarkers', () => {
  const raw: BroadcastRawItem[] = [
    { broadcast_id: 1, ts: '2026-07-11T23:00:00', kind: 'target', subject_name: 'Enemy', subject_ship: 'Rattlesnake', fight_id: 1 },
    { broadcast_id: 2, ts: '2026-07-11T23:00:05', kind: 'needs_shield', subject_name: 'Alice', subject_ship: 'Nighthawk', fight_id: 1 },
    { broadcast_id: 3, ts: '2026-07-11T23:00:10', kind: 'needs_armor', subject_name: 'Bob', subject_ship: 'Loki', fight_id: 1 },
  ]

  it('keeps only enabled kinds', () => {
    const m = toBroadcastMarkers(raw, new Set(['target', 'needs_armor']))
    expect(m.map((x) => x.kind)).toEqual(['target', 'needs_armor'])
    expect(m[0].label).toBe('Target: Enemy')
    expect(m[1].label).toBe('Armor: Bob')
  })

  it('returns nothing for an empty enabled set', () => {
    expect(toBroadcastMarkers(raw, new Set())).toHaveLength(0)
  })

  it('parses ts to epoch ms', () => {
    const m = toBroadcastMarkers(raw, new Set(['target']))
    expect(m[0].ts).toBe(Date.parse('2026-07-11T23:00:00'))
  })
})

function metrics(partial: Partial<BroadcastMetrics>): BroadcastMetrics {
  return {
    has_broadcasts: true,
    summary: {
      n_targets: 0, n_reps: 0, median_time_to_fire_s: null, compliance_rate: null,
      median_logi_response_s: null, median_reaction_s: null, median_damage_lead_s: null,
      late_broadcast_rate: null, false_broadcast_rate: null,
      deaths_total: 0, deaths_flagged: 0,
    },
    targets: { rows: [], per_pilot: [], compliance_rate: null, unanswered_count: 0 },
    reps: { rows: [], false_broadcast_rate: null, median_logi_response_s: null, median_damage_lead_s: null, late_broadcast_rate: null, unresolved_subjects: 0 },
    quality: {
      total_targets: 0, total_reps: 0, unanswered_targets: 0, false_reps: 0, late_broadcasts: 0,
      deaths_without_broadcast: 0, unresolved_rep_subjects: 0,
    },
    deaths: [],
    ...partial,
  }
}

describe('summarizeQuality', () => {
  it('converts rates to percentages and computes death-flagged share', () => {
    const m = metrics({
      summary: { ...metrics({}).summary, compliance_rate: 0.5, false_broadcast_rate: 0.667, deaths_total: 4, deaths_flagged: 3 },
      quality: { ...metrics({}).quality, unresolved_rep_subjects: 2 },
    })
    const s = summarizeQuality(m)
    expect(s.targetCompliancePct).toBe(50)
    expect(s.falseBroadcastPct).toBe(66.7)
    expect(s.deathsFlaggedPct).toBe(75)
    expect(s.unresolvedReps).toBe(2)
  })

  it('passes nulls through when data is absent', () => {
    const s = summarizeQuality(metrics({}))
    expect(s.targetCompliancePct).toBeNull()
    expect(s.deathsFlaggedPct).toBeNull()
  })
})

describe('flaggedDeathsByChar', () => {
  it('includes only flagged classifications', () => {
    const m = metrics({
      deaths: [
        { character_id: 1, character_name: 'A', ship: 'X', killmail_id: 1, ts: 't', fight_id: 1, classification: 'late_broadcast', last_broadcast_delta_s: 3 },
        { character_id: 2, character_name: 'B', ship: 'X', killmail_id: 2, ts: 't', fight_id: 1, classification: 'ok', last_broadcast_delta_s: 40 },
        { character_id: 3, character_name: 'C', ship: 'X', killmail_id: 3, ts: 't', fight_id: 1, classification: 'no_broadcast', last_broadcast_delta_s: null },
      ],
    })
    const flagged = flaggedDeathsByChar(m)
    expect(flagged.get(1)).toBe('late_broadcast')
    expect(flagged.has(2)).toBe(false)
    expect(flagged.get(3)).toBe('no_broadcast')
  })
})
