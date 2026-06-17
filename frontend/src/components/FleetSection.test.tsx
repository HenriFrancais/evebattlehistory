import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FleetTimeline } from '../api'
import { FleetSection } from './FleetSection'

// Mock uPlot to avoid canvas/matchMedia requirements in jsdom test environment.
// Capture the options passed at construction so tests can inspect plugins.
const uPlotConstructorCalls: { opts: object }[] = []
vi.mock('uplot', () => ({
  default: vi.fn().mockImplementation((opts: object) => {
    uPlotConstructorCalls.push({ opts })
    return {
      destroy: vi.fn(),
      setSize: vi.fn(),
      setSeries: vi.fn(),
    }
  }),
}))

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      fleetTimeline: vi.fn(),
    },
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

const fleetWithData: FleetTimeline = {
  x: [1000, 1005, 1010],
  series: [
    { key: 'dps_out', values: [100, 200, null] },
    { key: 'remote_rep', values: [50, null, 75] },
    { key: 'ewar', values: [1, 2, 3] },
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
    {
      ts: 1008,
      killmail_id: 43,
      victim_character_id: null,
      victim_ship_name: 'Loki',
      side_kind: 'friendly',
      isk: null,
    },
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

  it('shows loading state initially', async () => {
    vi.mocked(api.fleetTimeline).mockReturnValue(new Promise(() => {})) // never resolves

    render(<FleetSection brId="br1" />)

    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows empty state when fleet has no data points', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-empty')).toBeInTheDocument())
  })

  it('shows toggle buttons for EWAR and Cap Warfare series', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    expect(screen.getByRole('button', { name: /ewar/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cap warfare/i })).toBeInTheDocument()
  })

  it('EWAR toggle button changes aria-pressed when clicked', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)
    const user = userEvent.setup()

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    const ewarBtn = screen.getByRole('button', { name: /ewar/i })
    // Initially shown (aria-pressed=true or not pressed)
    expect(ewarBtn).toHaveAttribute('aria-pressed', 'true')

    await user.click(ewarBtn)

    expect(ewarBtn).toHaveAttribute('aria-pressed', 'false')
  })

  it('shows kill markers list with ship name and ISK', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-kills-list')).toBeInTheDocument())

    expect(screen.getByText('Tengu')).toBeInTheDocument()
    expect(screen.getByText('Loki')).toBeInTheDocument()
    // Tengu has 1.5B ISK
    expect(screen.getByText('1.50B')).toBeInTheDocument()
  })

  it('shows side_kind badge on kills', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-kills-list')).toBeInTheDocument())

    expect(screen.getByText('hostile')).toBeInTheDocument()
    expect(screen.getByText('friendly')).toBeInTheDocument()
  })

  it('shows no kills list when kills are empty', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue({
      ...fleetWithData,
      kills: [],
    })

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    expect(screen.queryByTestId('fleet-kills-list')).not.toBeInTheDocument()
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

  it('passes kills data to the chart via two plugins (fightMarkers + killMarkers)', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue(fleetWithData)

    render(<FleetSection brId="br1" />)

    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    // uPlot should have been constructed with exactly 2 plugins
    expect(uPlotConstructorCalls.length).toBeGreaterThanOrEqual(1)
    const lastCall = uPlotConstructorCalls[uPlotConstructorCalls.length - 1]
    const plugins = (lastCall.opts as { plugins?: unknown[] }).plugins
    expect(Array.isArray(plugins)).toBe(true)
    // fightMarkersPlugin + killMarkersPlugin
    expect(plugins?.length).toBe(2)
  })
})
