import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CharacterTimeline } from '../api'

// Mock the api module so no real fetches happen
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, characterTimeline: vi.fn() },
  }
})

// Note: ApiError is re-exported from the mocked module but the real class is used.
import { ApiError } from '../api'

// Mock the graph (uPlot/canvas) and capture its range callback.
let capturedOnSelectRange: ((r: { from: number; to: number }) => void) | null = null
vi.mock('../components/FleetGraph', () => ({
  FleetGraphCore: vi.fn(({ onSelectRange }: { onSelectRange: (r: { from: number; to: number }) => void }) => {
    capturedOnSelectRange = onSelectRange
    return <div data-testid="fleet-chart-area" />
  }),
  FleetGraph: vi.fn(() => <div data-testid="fleet-graph" />),
}))

// Mock the snapshot side-panel; record the props it receives.
let snapshotProps: { brId: string; charId?: string; range: unknown } | null = null
vi.mock('../components/SnapshotPanel', () => ({
  SnapshotPanel: vi.fn((props: { brId: string; charId?: string; range: unknown }) => {
    snapshotProps = props
    return <div data-testid="snapshot-panel" />
  }),
}))

import { api } from '../api'
import { CharacterTimelinePage } from './CharacterTimelinePage'

const mockTimeline: CharacterTimeline = {
  x: [1000, 2000, 3000],
  series: [
    { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [10, null, 30], event_count: 2 },
  ],
  fights: [
    { fight_id: 1, seq: 1, started_at: '2026-06-10T18:00:00Z', ended_at: '2026-06-10T18:30:00Z', system_id: 30000142 },
  ],
  t_start: 1000,
  t_end: 3000,
}

const mockEmptyTimeline: CharacterTimeline = { x: [], series: [], fights: [], t_start: null, t_end: null }

function renderPage() {
  capturedOnSelectRange = null
  snapshotProps = null
  return render(
    <MemoryRouter
      initialEntries={['/brs/br1/characters/12345']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/brs/:id/characters/:charId" element={<CharacterTimelinePage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('CharacterTimelinePage', () => {
  beforeEach(() => {
    vi.mocked(api.characterTimeline).mockReset()
  })

  it('renders the fleet-style graph and a char-scoped snapshot panel', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
    expect(screen.getByTestId('snapshot-panel')).toBeInTheDocument()
    // Snapshot is scoped to this character.
    expect(snapshotProps).toMatchObject({ brId: 'br1', charId: '12345' })
  })

  it('passes the selected range from the graph to the snapshot panel', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())

    expect(capturedOnSelectRange).not.toBeNull()
    await act(async () => { capturedOnSelectRange!({ from: 1000, to: 2000 }) })
    await waitFor(() => expect(snapshotProps?.range).toEqual({ from: 1000, to: 2000 }))
  })

  it('shows empty state when timeline has no series', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockEmptyTimeline)
    renderPage()
    await waitFor(() => expect(screen.getByText(/no logs for this character/i)).toBeInTheDocument())
    expect(screen.queryByTestId('fleet-chart-area')).not.toBeInTheDocument()
  })

  it('shows loading state then chart', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('fleet-chart-area')).toBeInTheDocument())
  })

  it('shows friendly 403 message when timeline fetch returns 403', async () => {
    vi.mocked(api.characterTimeline).mockRejectedValue(new ApiError(403, 'Access denied: not your character'))
    renderPage()
    await waitFor(() => expect(screen.getByTestId('forbidden-message')).toBeInTheDocument())
    expect(screen.queryByTestId('fleet-chart-area')).not.toBeInTheDocument()
  })
})
