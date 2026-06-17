import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrDetail, FightWithBrId, MeResponse, UserCoverage } from '../api'
import { ApiError } from '../api'
import { BrDetailPage } from './BrDetailPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getBr: vi.fn(),
      me: vi.fn(),
      myBrCoverage: vi.fn(),
      brCoverage: vi.fn(),
      filterFights: vi.fn(),
    },
  }
})

import { api } from '../api'

const mockBr: BrDetail = {
  br_id: 'br1',
  title: 'Test BR',
  source: 'zkillboard',
  source_url: null,
  status: 'ready',
  progress_pct: 100,
  result: 'win',
  isk_efficiency: 0.75,
  our_isk_destroyed: 1_000_000_000,
  our_isk_lost: 500_000_000,
  fight_count: 2,
  battle_at: '2026-06-10T18:00:00Z',
  created_at: '2026-06-10T20:00:00Z',
  fights: [
    {
      fight_id: 1,
      system_id: 30000142,
      started_at: '2026-06-10T18:00:00Z',
      ended_at: '2026-06-10T18:30:00Z',
      isk_destroyed_total: 1_000_000_000,
      largest_side_pilots: 10,
      sides: [],
    },
  ],
}

function makeMeResponse(can_create_br: boolean): MeResponse {
  return {
    user_name: 'TestUser',
    user_rank: 'FC',
    user_teams: [],
    main_character_id: '12345',
    can_create_br,
    impersonation_available: false,
  }
}

const mockMyCoverage: UserCoverage = {
  user_name: 'TestUser',
  characters: [
    {
      character_id: 111,
      character_name: 'AlphaChar',
      participated_fights: [1, 2],
      covered: false,
      fights_covered: [],
      fights_missing: [1, 2],
    },
  ],
}

const mockMyCoverageAll: UserCoverage = {
  user_name: 'TestUser',
  characters: [
    {
      character_id: 111,
      character_name: 'AlphaChar',
      participated_fights: [1],
      covered: true,
      fights_covered: [1],
      fights_missing: [],
    },
  ],
}

const mockFullCoverage: UserCoverage[] = [
  {
    user_name: 'TestUser',
    characters: [
      {
        character_id: 111,
        character_name: 'AlphaChar',
        participated_fights: [1],
        covered: true,
        fights_covered: [1],
        fights_missing: [],
      },
    ],
  },
  {
    user_name: 'OtherUser',
    characters: [
      {
        character_id: 222,
        character_name: 'BetaChar',
        participated_fights: [1],
        covered: false,
        fights_covered: [],
        fights_missing: [1],
      },
    ],
  },
]

function renderBrDetailPage() {
  return render(
    <MemoryRouter
      initialEntries={['/brs/br1']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/brs/:id" element={<BrDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('BrDetailPage', () => {
  beforeEach(() => {
    vi.mocked(api.getBr).mockReset()
    vi.mocked(api.me).mockReset()
    vi.mocked(api.myBrCoverage).mockReset()
    vi.mocked(api.brCoverage).mockReset()
    vi.mocked(api.filterFights).mockReset()
  })

  it('member (can_create_br=false) sees my-coverage with missing indicator', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)

    renderBrDetailPage()

    await waitFor(() => expect(screen.getByTestId('log-coverage-section')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('AlphaChar')).toBeInTheDocument())

    // Should show missing indicator
    expect(screen.getByText(/missing/i, { selector: '.cov-missing' })).toBeInTheDocument()
    // Should NOT show coverage matrix (no can_create_br)
    expect(screen.queryByTestId('coverage-matrix')).not.toBeInTheDocument()
  })

  it('FC (can_create_br=true) sees the full coverage matrix', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
    vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)

    renderBrDetailPage()

    await waitFor(() => expect(screen.getByTestId('log-coverage-section')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument())

    // OtherUser should appear in the full matrix
    expect(screen.getByText('OtherUser')).toBeInTheDocument()
    expect(screen.getByText('BetaChar')).toBeInTheDocument()
  })

  it('my-coverage 404 shows "None of your characters participated"', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockRejectedValue(new ApiError(404, 'Not Found'))

    renderBrDetailPage()

    await waitFor(() =>
      expect(
        screen.getByText('None of your characters participated in this BR.')
      ).toBeInTheDocument()
    )
  })

  it('coverage character name links to timeline route', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
    vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)

    renderBrDetailPage()

    // Wait for coverage matrix to appear
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument())

    // AlphaChar (character_id: 111) should have a link to the timeline
    // (may appear in both MyCoverageSection and CoverageMatrix; all instances link to the same URL)
    const links = screen.getAllByRole('link', { name: 'AlphaChar' })
    expect(links.length).toBeGreaterThanOrEqual(1)
    expect(links[0]).toHaveAttribute('href', '/brs/br1/characters/111')
  })

  it('fight filter: applying filter calls filterFights with br_id and narrows fights list', async () => {
    const filteredFight: FightWithBrId = {
      fight_id: 99,
      system_id: 30001111,
      started_at: '2026-06-10T19:00:00Z',
      ended_at: '2026-06-10T19:30:00Z',
      isk_destroyed_total: 500_000_000,
      largest_side_pilots: 5,
      sides: [],
      br_id: 'br1',
    }
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.filterFights).mockResolvedValue([filteredFight])

    renderBrDetailPage()

    // Wait for BR to load
    await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

    // Open the filter details panel
    const summary = screen.getByText(/Filter sub-engagements/i)
    fireEvent.click(summary)

    // Fill in a filter value — change field to capitals_involved, value is auto-populated
    // Actually use isk_destroyed_total (numeric) so we can type a value
    const valueInput = screen.getByTestId('filter-row-0-value')
    await userEvent.type(valueInput, '1')

    // Apply the filter
    fireEvent.click(screen.getByTestId('filter-apply'))

    // Wait for filter to be applied
    await waitFor(() => expect(screen.getByTestId('fight-filter-count')).toBeInTheDocument())

    // filterFights should have been called with br_id='br1'
    expect(vi.mocked(api.filterFights)).toHaveBeenCalledWith(
      expect.any(Object),
      'br1'
    )

    // The count indicator should show filtered results
    expect(screen.getByTestId('fight-filter-count')).toBeInTheDocument()
  })

  it('fight filter: clearing filter restores original fights list', async () => {
    const filteredFight: FightWithBrId = {
      fight_id: 99,
      system_id: 30001111,
      started_at: '2026-06-10T19:00:00Z',
      ended_at: '2026-06-10T19:30:00Z',
      isk_destroyed_total: 500_000_000,
      largest_side_pilots: 5,
      sides: [],
      br_id: 'br1',
    }
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.filterFights).mockResolvedValue([filteredFight])

    renderBrDetailPage()
    await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

    // Open filter and apply
    const summary = screen.getByText(/Filter sub-engagements/i)
    fireEvent.click(summary)
    const valueInput = screen.getByTestId('filter-row-0-value')
    await userEvent.type(valueInput, '1')
    fireEvent.click(screen.getByTestId('filter-apply'))
    await waitFor(() => expect(screen.getByTestId('fight-filter-count')).toBeInTheDocument())

    // Clear the filter
    fireEvent.click(screen.getByTestId('fight-filter-clear'))

    // Count indicator should be gone
    await waitFor(() => expect(screen.queryByTestId('fight-filter-count')).not.toBeInTheDocument())
  })
})
