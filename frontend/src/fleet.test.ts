import { describe, expect, it } from 'vitest'
import type { FleetTimeline } from './api'
import { toFleetUplotData } from './fleet'

const emptyFleet: FleetTimeline = {
  x: [],
  series: [],
  kills: [],
  fights: [],
  bucket_seconds: 5,
  t_start: null,
  t_end: null,
}

const fleetWithData: FleetTimeline = {
  x: [1000, 1005, 1010],
  series: [
    { key: 'dps_out', values: [100, 200, null] },
    { key: 'remote_rep', values: [50, null, 75] },
    { key: 'ewar', values: [null, null, null] },
    { key: 'cap_warfare', values: [10, 20, 30] },
  ],
  kills: [
    {
      ts: 1005,
      killmail_id: 42,
      victim_character_id: 999,
      victim_ship_name: 'Tengu',
      side_kind: 'hostile',
      isk: 1_500_000_000,
    },
  ],
  fights: [],
  bucket_seconds: 5,
  t_start: 1000,
  t_end: 1010,
}

describe('toFleetUplotData', () => {
  it('empty fleet → 4 series configs always present', () => {
    const result = toFleetUplotData(emptyFleet)
    expect(result.seriesConfig).toHaveLength(4)
    const keys = result.seriesConfig.map((s) => s.key)
    expect(keys).toContain('dps_out')
    expect(keys).toContain('remote_rep')
    expect(keys).toContain('ewar')
    expect(keys).toContain('cap_warfare')
  })

  it('empty fleet → data has 5 arrays (x + 4 series)', () => {
    const result = toFleetUplotData(emptyFleet)
    expect(result.data).toHaveLength(5)
    expect(result.data[0]).toEqual([]) // x timestamps
  })

  it('values aligned to correct series position', () => {
    const result = toFleetUplotData(fleetWithData)
    // data[0] = x, data[1] = dps_out, data[2] = remote_rep, data[3] = ewar, data[4] = cap_warfare
    expect(result.data[0]).toEqual([1000, 1005, 1010])
    expect(result.data[1]).toEqual([100, 200, null]) // dps_out
    expect(result.data[2]).toEqual([50, null, 75])   // remote_rep
    expect(result.data[4]).toEqual([10, 20, 30])     // cap_warfare
  })

  it('null gaps are preserved (not filled with 0)', () => {
    const result = toFleetUplotData(fleetWithData)
    // ewar is all null
    expect(result.data[3]).toEqual([null, null, null])
    // remote_rep has a gap at index 1
    expect(result.data[2][1]).toBeNull()
  })

  it('missing series filled with nulls of correct length', () => {
    const partialFleet: FleetTimeline = {
      ...fleetWithData,
      series: [{ key: 'dps_out', values: [100, 200, 300] }],
    }
    const result = toFleetUplotData(partialFleet)
    // remote_rep not provided → all nulls, length 3
    expect(result.data[2]).toEqual([null, null, null])
    // ewar not provided → all nulls
    expect(result.data[3]).toEqual([null, null, null])
    // cap_warfare not provided → all nulls
    expect(result.data[4]).toEqual([null, null, null])
  })

  it('ewar and cap_warfare series are marked toggleable=true', () => {
    const result = toFleetUplotData(fleetWithData)
    const ewar = result.seriesConfig.find((s) => s.key === 'ewar')
    const cap = result.seriesConfig.find((s) => s.key === 'cap_warfare')
    const dps = result.seriesConfig.find((s) => s.key === 'dps_out')
    const rep = result.seriesConfig.find((s) => s.key === 'remote_rep')

    expect(ewar?.toggleable).toBe(true)
    expect(cap?.toggleable).toBe(true)
    expect(dps?.toggleable).toBe(false)
    expect(rep?.toggleable).toBe(false)
  })

  it('kills are passed through with correct side_kind', () => {
    const result = toFleetUplotData(fleetWithData)
    expect(result.kills).toHaveLength(1)
    expect(result.kills[0].killmail_id).toBe(42)
    expect(result.kills[0].side_kind).toBe('hostile')
    expect(result.kills[0].victim_ship_name).toBe('Tengu')
    expect(result.kills[0].isk).toBe(1_500_000_000)
  })

  it('returns correct shape { data, seriesConfig, kills }', () => {
    const result = toFleetUplotData(emptyFleet)
    expect(result).toHaveProperty('data')
    expect(result).toHaveProperty('seriesConfig')
    expect(result).toHaveProperty('kills')
    expect(Array.isArray(result.data)).toBe(true)
    expect(Array.isArray(result.seriesConfig)).toBe(true)
    expect(Array.isArray(result.kills)).toBe(true)
  })

  it('series config has correct labels', () => {
    const result = toFleetUplotData(emptyFleet)
    const labels = result.seriesConfig.map((s) => s.label)
    expect(labels).toContain('Fleet DPS')
    expect(labels).toContain('Remote Rep')
    expect(labels).toContain('EWAR (count)')
    expect(labels).toContain('Cap Warfare')
  })
})
