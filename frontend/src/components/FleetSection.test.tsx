import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FleetTimeline } from '../api'
import { FleetSection } from './FleetSection'

// Mock uPlot to avoid canvas/matchMedia requirements in jsdom.
const uPlotConstructorCalls: { opts: object }[] = []
vi.mock('uplot', () => ({
  default: vi.fn().mockImplementation((opts: object) => {
    uPlotConstructorCalls.push({ opts })
    return { destroy: vi.fn(), setSize: vi.fn(), setSeries: vi.fn() }
  }),
}))

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, fleetTimeline: vi.fn() },
  }
})

import { api } from '../api'

const emptyFleet: FleetTimeline = {
  x: [],
  series: [],
  kills: [],
  fights: [],
  bucket_seconds: 5,
  t_start: null,
  t_end: null,
}

function mk(effect_type: string, direction: string, values: (number | null)[]) {
  const metric = ['scram', 'disrupt', 'jam'].includes(effect_type) ? 'count' : 'amount'
  return { key: `${effect_type}:${direction}`, effect_type, direction, metric, values }
}

const fleetWithData: FleetTimeline = {
  x: [1000, 1005, 1010],
  series: [
    mk('damage', 'out', [100, 200, 150]),
    mk('damage', 'in', [10, 20, 30]),
    mk('rep_armor', 'in', [50, 60, 70]),
    mk('neut', 'out', [5, 5, 5]),
    mk('scram', 'out', [1, 1, 1]),
    mk('scram', 'in', [2, 2, 2]),
  ],
  kills: [
    { ts: 1005, killmail_id: 42, victim_character_id: 999, victim_character_name: 'Tengu Pilot', victim_ship_name: 'Tengu', victim_ship_type_id: 17738, side_kind: 'hostile', isk: 1_500_000_000 },
    { ts: 1008, killmail_id: 43, victim_character_id: null, victim_character_name: null, victim_ship_name: 'Loki', victim_ship_type_id: 29990, side_kind: 'friendly', isk: null },
  ],
  fights: [],
  bucket_seconds: 5,
  t_start: 1000,
  t_end: 1010,
}

describe('FleetSection', () => {
  beforeEach(() => {
    vi.mocked(api.fleetTimeline).mockReset()
    uPlotConstructorCalls.length = 0
  })

  it('shows loading state initially', () => {
    vi.mocked(api.fleetTimeline).mockReturnValue(new Promise(() => {}))
    render(<FleetSection brId="br1" />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows empty state when fleet has no data points', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-empty')).toBeInTheDocument())
  })

  it('renders the three effect panels', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    expect(screen.getByTestId('fleet-panel-damage')).toBeInTheDocument()
    expect(screen.getByTestId('fleet-panel-cap')).toBeInTheDocument()
    expect(screen.getByTestId('fleet-panel-ewar')).toBeInTheDocument()
  })

  it('shows curated family toggle buttons grouped by panel', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Damage applied/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Damage received/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Rep received/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Neut\/NOS applied/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tackle applied/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tackle received/i })).toBeInTheDocument()
  })

  it('toggling a series flips aria-pressed', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    const user = userEvent.setup()
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    const btn = screen.getByRole('button', { name: /Damage applied/i })
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'false')
  })

  it('has a smoothing toggle that flips', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    const user = userEvent.setup()
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    const btn = screen.getByRole('button', { name: /smoothing/i })
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'false')
  })

  it('shows a kill legend with side-A semantics and counts (no kills table)', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-kill-legend')).toBeInTheDocument())
    expect(screen.getByText(/enemy lost \(1\)/i)).toBeInTheDocument()
    expect(screen.getByText(/friendly lost \(1\)/i)).toBeInTheDocument()
  })

  it('has a kill-markers toggle that flips', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    const user = userEvent.setup()
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    const btn = screen.getByRole('button', { name: /kill markers/i })
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-pressed', 'false')
  })

  it('shows no kill legend when kills are empty', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue({ ...fleetWithData, kills: [] })
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    expect(screen.queryByTestId('fleet-kill-legend')).not.toBeInTheDocument()
  })

  it('shows error state when API call fails', async () => {
    vi.mocked(api.fleetTimeline).mockRejectedValue(new Error('Network error'))
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-error')).toBeInTheDocument())
    expect(screen.getByText(/network error/i)).toBeInTheDocument()
  })

  it('calls fleetTimeline with the correct brId', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
    render(<FleetSection brId="mybr42" />)
    await waitFor(() => expect(vi.mocked(api.fleetTimeline)).toHaveBeenCalledWith('mybr42'))
  })

  it('constructs a uPlot per visible panel with kill + baseline plugins', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    render(<FleetSection brId="br1" />)
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    // 3 panels → at least 3 uPlot constructions
    expect(uPlotConstructorCalls.length).toBeGreaterThanOrEqual(3)
    const plugins = (uPlotConstructorCalls.at(-1)!.opts as { plugins?: unknown[] }).plugins
    expect(Array.isArray(plugins)).toBe(true)
    expect(plugins!.length).toBe(4) // fightEdges + zeroBaseline + killMarkers + slider
  })
})
