import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrDetail, MeResponse, UserCoverage } from '../api'
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
})
