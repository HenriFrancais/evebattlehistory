// Pure transform: CharacterTimeline API response → FleetTimeline shape so the
// shared FleetGraphCore can render a single pilot. Unit-tested in jsdom (no canvas).

import type { CharacterTimeline, FleetTimeline } from './api'

/** Smallest positive gap between consecutive x values (bucket width), or 5s default. */
function deriveBucketSeconds(x: number[]): number {
  let min = Infinity
  for (let i = 1; i < x.length; i++) {
    const d = x[i] - x[i - 1]
    if (d > 0 && d < min) min = d
  }
  return Number.isFinite(min) ? min : 5
}

/**
 * Adapt a per-character timeline into the FleetTimeline shape so the shared
 * FleetGraphCore (families/smoothing/toggles) can render it. No kill markers
 * (those are fleet-level). The effect_type/direction vocabulary matches the
 * fleet families, so toFleetView groups them identically.
 */
export function toFleetTimeline(ct: CharacterTimeline): FleetTimeline {
  return {
    x: ct.x,
    series: ct.series.map((s) => ({
      key: s.key,
      effect_type: s.effect_type ?? '',
      direction: s.direction ?? '',
      metric: 'amount',
      values: s.values,
    })),
    kills: [],
    fights: ct.fights,
    bucket_seconds: deriveBucketSeconds(ct.x),
    t_start: ct.t_start,
    t_end: ct.t_end,
  }
}
