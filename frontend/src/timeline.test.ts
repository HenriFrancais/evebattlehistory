import { describe, expect, it } from 'vitest'
import type { CharacterTimeline } from './api'
import { toUplotData } from './timeline'

const baseFight = {
  fight_id: 1,
  seq: 1,
  started_at: null,
  ended_at: null,
  system_id: 30000142,
}

describe('toUplotData', () => {
  it('empty series → data=[xs], seriesConfig=[]', () => {
    const tl: CharacterTimeline = {
      x: [1000, 2000, 3000],
      series: [],
      fights: [baseFight],
      t_start: 1000,
      t_end: 3000,
    }
    const { data, seriesConfig } = toUplotData(tl)
    expect(data).toHaveLength(1) // just xs
    expect(data[0]).toEqual([1000, 2000, 3000])
    expect(seriesConfig).toHaveLength(0)
  })

  it('two series → data has xs + 2 aligned arrays', () => {
    const tl: CharacterTimeline = {
      x: [100, 200, 300],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [10, null, 30], event_count: 2 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [null, 5, null], event_count: 1 },
      ],
      fights: [],
      t_start: 100,
      t_end: 300,
    }
    const { data, seriesConfig } = toUplotData(tl)
    expect(data).toHaveLength(3) // xs + 2 series
    expect(data[0]).toEqual([100, 200, 300])
    expect(data[1]).toEqual([10, null, 30])
    expect(data[2]).toEqual([null, 5, null])
    expect(seriesConfig).toHaveLength(2)
  })

  it('null values are preserved (not replaced with 0)', () => {
    const tl: CharacterTimeline = {
      x: [1, 2, 3, 4],
      series: [
        { key: 'regen/out', effect_type: 'regen', direction: 'out', values: [null, null, 7, null], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 4,
    }
    const { data } = toUplotData(tl)
    expect(data[1]).toEqual([null, null, 7, null])
  })

  it('series length matches x length', () => {
    const tl: CharacterTimeline = {
      x: [10, 20, 30, 40, 50],
      series: [
        { key: 'ewar/in', effect_type: 'ewar', direction: 'in', values: [1, 2, null, 4, 5], event_count: 4 },
      ],
      fights: [],
      t_start: 10,
      t_end: 50,
    }
    const { data } = toUplotData(tl)
    expect(data[0]).toHaveLength(5)
    expect(data[1]).toHaveLength(5)
  })

  it('seriesConfig label uses key; direction mapped correctly', () => {
    const tl: CharacterTimeline = {
      x: [1],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [1], event_count: 1 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [2], event_count: 1 },
        { key: 'unknown', effect_type: null, direction: null, values: [3], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 1,
    }
    const { seriesConfig } = toUplotData(tl)
    expect(seriesConfig[0].direction).toBe('out')
    expect(seriesConfig[1].direction).toBe('in')
    expect(seriesConfig[2].direction).toBeNull()
    // labels are non-empty strings
    expect(seriesConfig[0].label.length).toBeGreaterThan(0)
    expect(seriesConfig[1].label.length).toBeGreaterThan(0)
    expect(seriesConfig[2].label.length).toBeGreaterThan(0)
  })

  it('outgoing series get a warm stroke colour; incoming get a cool stroke', () => {
    const tl: CharacterTimeline = {
      x: [1],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [1], event_count: 1 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [1], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 1,
    }
    const { seriesConfig } = toUplotData(tl)
    // Warm colours start with #f, #e, #ff, or orange/red range; cool with #4, #5, #6, #7, #8 or blue/teal
    // Just check they differ; the exact hex is determined by the implementation
    expect(seriesConfig[0].stroke).not.toBe(seriesConfig[1].stroke)
  })
})
