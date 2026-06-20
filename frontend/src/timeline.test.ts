import { describe, expect, it } from 'vitest'
import type { CharacterTimeline } from './api'
import { toFleetTimeline } from './timeline'

describe('toFleetTimeline', () => {
  it('adapts a character timeline into the fleet shape (no kills, derived bucket)', () => {
    const ct: CharacterTimeline = {
      x: [1000, 1005, 1010],
      series: [
        { key: 'damage:out', effect_type: 'damage', direction: 'out', values: [1, 2, 3], event_count: 3 },
        { key: 'unknown', effect_type: null, direction: null, values: [null, 1, null], event_count: 1 },
      ],
      fights: [{ fight_id: 1, seq: 1, started_at: null, ended_at: null, system_id: 30000142 }],
      t_start: 1000,
      t_end: 1010,
    }
    const fl = toFleetTimeline(ct)
    expect(fl.kills).toEqual([])
    expect(fl.bucket_seconds).toBe(5) // smallest positive gap
    expect(fl.x).toEqual([1000, 1005, 1010])
    expect(fl.series[0]).toMatchObject({ effect_type: 'damage', direction: 'out', metric: 'amount' })
    // null effect/direction collapse to '' (not a graph family)
    expect(fl.series[1]).toMatchObject({ effect_type: '', direction: '' })
    expect(fl.fights).toHaveLength(1)
  })

  it('falls back to 5s bucket when x has no positive gaps', () => {
    const ct: CharacterTimeline = { x: [], series: [], fights: [], t_start: null, t_end: null }
    expect(toFleetTimeline(ct).bucket_seconds).toBe(5)
  })
})
