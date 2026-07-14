import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BrPerformance, PerfCharRow } from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, performance: vi.fn() } }
})
import { api } from '../api'
import { PerformancePanel } from './PerformancePanel'

function row(partial: Partial<PerfCharRow>): PerfCharRow {
  return {
    character_id: 1, character_name: 'Alice', user_name: 'Alice', is_self: false,
    damage_done: 1000, reps_out: 0, kills_on: 2, has_logs: true,
    target_calls_engaged: 3, target_median_switch_s: 5, target_compliance_rate: 0.8,
    logi_response_median_s: null, damage_lead_median_s: null, late_broadcasts: 0,
    rep_broadcasts: 0, false_broadcasts: 0,
    deaths: 0, deaths_flagged: 0,
    ...partial,
  }
}

function perf(partial: Partial<BrPerformance>): BrPerformance {
  return {
    elevated: false,
    has_broadcasts: true,
    summary: {
      n_targets: 0, n_reps: 0, median_time_to_fire_s: null, compliance_rate: null,
      median_logi_response_s: null, median_reaction_s: null, median_damage_lead_s: null,
      late_broadcast_rate: null, false_broadcast_rate: null,
      deaths_total: 0, deaths_flagged: 0,
    },
    distributions: { target_switch_s: [4, 6, 8, 10], logi_response_s: [], damage_lead_s: [] },
    characters: [],
    ...partial,
  }
}

beforeEach(() => vi.mocked(api.performance).mockReset())

describe('PerformancePanel', () => {
  it('shows only the viewer own rows and a percentile for non-elevated users', async () => {
    vi.mocked(api.performance).mockResolvedValue(
      perf({ elevated: false, characters: [row({ character_id: 1, character_name: 'Me Main', is_self: true, target_median_switch_s: 5 })] }),
    )
    render(<PerformancePanel brId="br1" />)
    await screen.findByTestId('performance-panel')
    expect(screen.getByTestId('my-performance')).toBeInTheDocument()
    // Percentile: 5s beats the two values >5 (6,8,10... actually 8,10 and 6) = 3 of 4 = 75%.
    expect(screen.getByText(/faster than 75% of the fleet/)).toBeInTheDocument()
    // No FC/HC fleet table for non-elevated.
    expect(screen.queryByTestId('fleet-performance')).not.toBeInTheDocument()
  })

  it('shows the full sortable fleet table for elevated users', async () => {
    vi.mocked(api.performance).mockResolvedValue(
      perf({
        elevated: true,
        characters: [
          row({ character_id: 1, character_name: 'AltChar', user_name: 'Alice', is_self: false, damage_done: 500 }),
          row({ character_id: 2, character_name: 'Me Main', user_name: 'Me Main', is_self: true, damage_done: 900 }),
        ],
      }),
    )
    render(<PerformancePanel brId="br1" />)
    await screen.findByTestId('performance-panel')
    expect(screen.getByTestId('fleet-performance')).toBeInTheDocument()
    expect(screen.getByText('AltChar')).toBeInTheDocument() // alt character column
    expect(screen.getByText('Alice')).toBeInTheDocument() // owning main column
  })

  it('renders an empty state when there are no character rows', async () => {
    vi.mocked(api.performance).mockResolvedValue(perf({ characters: [] }))
    render(<PerformancePanel brId="br1" />)
    await screen.findByTestId('performance-empty')
  })
})
