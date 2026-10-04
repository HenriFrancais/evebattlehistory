// Pure ordering / grouping for the Involved tab. A pilot who reshipped has one row
// per hull flown.
import type { CompositionPilot, CompositionSide } from './api'

const UNKNOWN_RANK = 999

/**
 * Largest hull class first (capitals down to frigates and pods), the same hulls
 * together, then the bigger damage dealer, then by name.
 */
export function sortPilots(pilots: CompositionPilot[]): CompositionPilot[] {
  return [...pilots].sort(
    (a, b) =>
      (a.ship_rank ?? UNKNOWN_RANK) - (b.ship_rank ?? UNKNOWN_RANK) ||
      a.ship_name.localeCompare(b.ship_name) ||
      b.damage_done - a.damage_done ||
      a.character_name.localeCompare(b.character_name),
  )
}

export interface MainGroup {
  /** NV Tools user (the main) the characters belong to. */
  user: string
  /** False for the trailing group of characters matched to no roster user. */
  known: boolean
  pilots: CompositionPilot[]
}

/** One side's pilots by the main they belong to (FC/HC only); unmatched pilots last. */
export function groupByMain(side: CompositionSide): MainGroup[] {
  const m = new Map<string, CompositionPilot[]>()
  for (const p of side.pilots) {
    const key = p.user_name ?? ''
    const arr = m.get(key)
    if (arr) arr.push(p)
    else m.set(key, [p])
  }
  return [...m.entries()]
    .sort((a, b) => Number(a[0] === '') - Number(b[0] === '') || a[0].localeCompare(b[0]))
    .map(([user, pilots]) => ({
      user: user || 'Not on the roster',
      known: user !== '',
      pilots: sortPilots(pilots),
    }))
}
