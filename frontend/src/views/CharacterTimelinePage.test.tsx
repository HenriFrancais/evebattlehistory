import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CharacterTimeline, TimelineEventList } from '../api'

// Mock the entire api module so no real fetches happen
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      characterTimeline: vi.fn(),
      characterEvents: vi.fn(),
    },
  }
})

// Note: ApiError is re-exported from the mocked module but the real class is used.
import { ApiError } from '../api'

// Mock TimelineChart to avoid uPlot/canvas in tests.
// The mock renders series labels as data-testid attributes and exposes
// a way to trigger onSelectRange.
let capturedOnSelectRange: ((from: number, to: number) => void) | null = null

vi.mock('../components/TimelineChart', () => ({
  TimelineChart: vi.fn(({ data, onSelectRange }: { data: { seriesConfig: Array<{ label: string }> }; onSelectRange: (from: number, to: number) => void }) => {
    capturedOnSelectRange = onSelectRange
    return (
      <div data-testid="timeline-chart">
        {data.seriesConfig.map((s: { label: string }) => (
          <span key={s.label} data-testid="series-label">{s.label}</span>
        ))}
      </div>
    )
  }),
}))

import { api } from '../api'
import { CharacterTimelinePage } from './CharacterTimelinePage'

const mockTimeline: CharacterTimeline = {
  x: [1000, 2000, 3000],
  series: [
    { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [10, null, 30], event_count: 2 },
    { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [null, 5, null], event_count: 1 },
  ],
  fights: [
    { fight_id: 1, seq: 1, started_at: '2026-06-10T18:00:00Z', ended_at: '2026-06-10T18:30:00Z', system_id: 30000142 },
  ],
  t_start: 1000,
  t_end: 3000,
}

const mockEmptyTimeline: CharacterTimeline = {
  x: [],
  series: [],
  fights: [],
  t_start: null,
  t_end: null,
}

const mockEventList: TimelineEventList = {
  events: [
    {
      ts: '2026-06-10T18:05:00Z',
      direction: 'out',
      effect_type: 'damage',
      amount: 450.5,
      quality: null,
      other_name: 'EnemyPilot',
      other_ship_name: 'Drake',
      module_name: 'Heavies',
    },
  ],
  truncated: false,
}

const mockEventListTruncated: TimelineEventList = {
  events: mockEventList.events,
  truncated: true,
}

function renderPage() {
  capturedOnSelectRange = null
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
    vi.mocked(api.characterEvents).mockReset()
  })

  it('renders series labels after loading timeline', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    const labels = screen.getAllByTestId('series-label').map((el) => el.textContent)
    expect(labels).toContain('damage out')
    expect(labels).toContain('damage in')
  })

  it('shows empty state when timeline has no series', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockEmptyTimeline)
    renderPage()

    await waitFor(() =>
      expect(screen.getByText(/no logs for this character/i)).toBeInTheDocument()
    )
    expect(screen.queryByTestId('timeline-chart')).not.toBeInTheDocument()
  })

  it('brush-select triggers events fetch and renders events panel', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    vi.mocked(api.characterEvents).mockResolvedValue(mockEventList)

    renderPage()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    // Simulate brush-select
    expect(capturedOnSelectRange).not.toBeNull()
    await act(async () => {
      capturedOnSelectRange!(1000, 2000)
    })

    await waitFor(() => expect(api.characterEvents).toHaveBeenCalledWith('br1', '12345', 1000, 2000))
    await waitFor(() => expect(screen.getByText('EnemyPilot')).toBeInTheDocument())
  })

  it('shows truncated notice when events are truncated', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    vi.mocked(api.characterEvents).mockResolvedValue(mockEventListTruncated)

    renderPage()
    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    await act(async () => {
      capturedOnSelectRange!(1000, 3000)
    })

    await waitFor(() => expect(screen.getByText(/truncated/i)).toBeInTheDocument())
  })

  it('shows loading state then chart', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()

    expect(screen.getByText(/loading/i)).toBeInTheDocument()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
  })

  it('shows friendly 403 message when timeline fetch returns 403', async () => {
    vi.mocked(api.characterTimeline).mockRejectedValue(new ApiError(403, 'Access denied: not your character'))
    renderPage()

    await waitFor(() =>
      expect(screen.getByTestId('forbidden-message')).toBeInTheDocument()
    )
    expect(screen.getByText(/you can only view your own characters/i)).toBeInTheDocument()
    expect(screen.queryByTestId('timeline-chart')).not.toBeInTheDocument()
  })
})
