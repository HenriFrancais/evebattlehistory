import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrDetail, BrSourceOut, MeResponse, UserCoverage } from '../api'
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
      fleetTimeline: vi.fn(),
      composition: vi.fn(),
      snapshot: vi.fn(),
      getSources: vi.fn(),
      patchBrTitle: vi.fn(),
      addSource: vi.fn(),
      deleteSource: vi.fn(),
      refreshBr: vi.fn(),
      deleteBr: vi.fn(),
      getBrStatus: vi.fn(),
    },
  }
})

// Mock uPlot to avoid canvas/matchMedia requirements in jsdom test environment.
vi.mock('uplot', () => ({
  default: vi.fn().mockImplementation(() => ({
    destroy: vi.fn(),
    setSize: vi.fn(),
  })),
}))

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
  systems: [],
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
  const emptyFleet = { x: [], series: [], kills: [], fights: [], bucket_seconds: 5, t_start: null, t_end: null, leaders: [] }

  beforeEach(() => {
    vi.mocked(api.getBr).mockReset()
    vi.mocked(api.me).mockReset()
    vi.mocked(api.myBrCoverage).mockReset()
    vi.mocked(api.brCoverage).mockReset()
    vi.mocked(api.filterFights).mockReset()
    vi.mocked(api.fleetTimeline).mockReset()
    vi.mocked(api.composition).mockReset()
    vi.mocked(api.snapshot).mockReset()
    vi.mocked(api.getSources).mockReset()
    vi.mocked(api.patchBrTitle).mockReset()
    vi.mocked(api.addSource).mockReset()
    vi.mocked(api.deleteSource).mockReset()
    vi.mocked(api.refreshBr).mockReset()
    vi.mocked(api.deleteBr).mockReset()
    vi.mocked(api.getBrStatus).mockReset()
    // Defaults for non-critical calls
    vi.mocked(api.getSources).mockResolvedValue([])
    vi.mocked(api.getBrStatus).mockResolvedValue({ br_id: 'br1', status: 'ready', progress_pct: 100, error_text: null })
    vi.mocked(api.composition).mockResolvedValue({ by_user_available: false, sides: [] })
    vi.mocked(api.snapshot).mockResolvedValue({ from_ts: 0, to_ts: 0, rows: [] })
  })

  it('member (can_create_br=false) sees my-coverage with missing indicator', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

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
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

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
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

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
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

    renderBrDetailPage()

    // Wait for coverage matrix to appear
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument())

    // AlphaChar (character_id: 111) should have a link to the timeline
    // (may appear in both MyCoverageSection and CoverageMatrix; all instances link to the same URL)
    const links = screen.getAllByRole('link', { name: 'AlphaChar' })
    expect(links.length).toBeGreaterThanOrEqual(1)
    expect(links[0]).toHaveAttribute('href', '/brs/br1/characters/111')
  })

  it('lays out fleet graph and detail rail in two columns', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

    renderBrDetailPage()
    await waitFor(() => expect(screen.getByTestId('br-detail-grid')).toBeInTheDocument())
    expect(screen.getByTestId('br-col-main')).toBeInTheDocument()
    expect(screen.getByTestId('br-col-side')).toBeInTheDocument()
    // moment detail starts in its empty state
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
  })

  it('expands the fleet graph into a fullscreen overlay and closes it', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

    const user = userEvent.setup()
    renderBrDetailPage()
    await waitFor(() => expect(screen.getByTestId('expand-graph-btn')).toBeInTheDocument())

    expect(screen.queryByTestId('graph-overlay')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('expand-graph-btn'))
    expect(screen.getByTestId('graph-overlay')).toBeInTheDocument()

    await user.click(screen.getByTestId('close-graph-btn'))
    expect(screen.queryByTestId('graph-overlay')).not.toBeInTheDocument()
  })

  it('shows Summary then Sides, no Engagements/filter, Snapshot in the rail', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

    renderBrDetailPage()
    await waitFor(() => expect(screen.getByTestId('br-detail-grid')).toBeInTheDocument())
    expect(screen.getByTestId('summary-section')).toBeInTheDocument()
    expect(screen.getByTestId('sides-section')).toBeInTheDocument()
    expect(screen.queryByText(/Filter sub-engagements/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /^Engagements$/i })).not.toBeInTheDocument()
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
  })

  // -------------------------------------------------------------------------
  // E4b: Editable title
  // -------------------------------------------------------------------------

  describe('editable title', () => {
    it('can_create_br user sees an edit button next to the title', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

      expect(screen.getByTestId('edit-title-btn')).toBeInTheDocument()
    })

    it('non-creator does not see an edit button', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

      expect(screen.queryByTestId('edit-title-btn')).not.toBeInTheDocument()
    })

    it('clicking edit shows input, saving calls patchBrTitle and updates header', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.patchBrTitle).mockResolvedValue({ ...mockBr, title: 'Updated Title' })

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

      fireEvent.click(screen.getByTestId('edit-title-btn'))

      const titleInput = await screen.findByTestId('title-input')
      expect(titleInput).toHaveValue('Test BR')

      await userEvent.clear(titleInput)
      await userEvent.type(titleInput, 'Updated Title')

      fireEvent.click(screen.getByTestId('save-title-btn'))

      await waitFor(() => expect(vi.mocked(api.patchBrTitle)).toHaveBeenCalledWith('br1', 'Updated Title'))
      await waitFor(() => expect(screen.getByText('Updated Title')).toBeInTheDocument())
      expect(screen.queryByTestId('title-input')).not.toBeInTheDocument()
    })
  })

  // -------------------------------------------------------------------------
  // E4b: Sources panel
  // -------------------------------------------------------------------------

  describe('sources panel', () => {
    const mockSources: BrSourceOut[] = [
      {
        source_id: 1,
        br_id: 'br1',
        kind: 'link',
        url: 'https://zkillboard.com/related/30004759/202606101800/',
        system_id: null,
        window_start: null,
        window_end: null,
        label: null,
        status: 'ready',
        error_text: null,
        km_count: 42,
      },
    ]

    it('sources panel is visible for can_create_br user and lists sources with status + km_count', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.getSources).mockResolvedValue(mockSources)

      renderBrDetailPage()

      await waitFor(() => expect(screen.getByTestId('sources-panel')).toBeInTheDocument())
      expect(screen.getByText(/42 km/)).toBeInTheDocument()    // km_count
      expect(screen.getAllByText(/ready/i).length).toBeGreaterThan(0)
    })

    it('sources panel is hidden for non-creator', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())

      expect(screen.queryByTestId('sources-panel')).not.toBeInTheDocument()
    })

    it('delete source button calls api.deleteSource', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.getSources).mockResolvedValue(mockSources)
      vi.mocked(api.deleteSource).mockResolvedValue()
      vi.mocked(api.refreshBr).mockResolvedValue({ br_id: 'br1', status: 'pending', progress_pct: 0, error_text: null })

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByTestId('sources-panel')).toBeInTheDocument())

      fireEvent.click(screen.getByTestId('delete-source-1'))

      await waitFor(() =>
        expect(vi.mocked(api.deleteSource)).toHaveBeenCalledWith('br1', 1)
      )
    })

    it('add source form calls api.addSource', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.getSources).mockResolvedValue([])
      vi.mocked(api.addSource).mockResolvedValue({ br_id: 'br1', status: 'pending' })
      vi.mocked(api.refreshBr).mockResolvedValue({ br_id: 'br1', status: 'pending', progress_pct: 0, error_text: null })

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByTestId('sources-panel')).toBeInTheDocument())

      // Open the add-source form (expand details)
      const addDetails = screen.getByTestId('add-source-details')
      fireEvent.click(addDetails.querySelector('summary')!)

      // Fill in a link URL
      const urlInput = screen.getByTestId('add-source-url')
      fireEvent.change(urlInput, { target: { value: 'https://zkillboard.com/related/30004759/202606101800/' } })

      fireEvent.click(screen.getByTestId('add-source-submit'))

      await waitFor(() =>
        expect(vi.mocked(api.addSource)).toHaveBeenCalledWith('br1', {
          kind: 'link',
          url: 'https://zkillboard.com/related/30004759/202606101800/',
        })
      )
    })
  })

  // -------------------------------------------------------------------------
  // E4b: Refresh button
  // -------------------------------------------------------------------------

  describe('refresh button', () => {
    it('can_create_br user sees the Refresh button', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByTestId('refresh-btn')).toBeInTheDocument())
    })

    it('non-creator does not see the Refresh button', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())
      expect(screen.queryByTestId('refresh-btn')).not.toBeInTheDocument()
    })

    it('clicking Refresh calls api.refreshBr and shows IngestProgress', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.refreshBr).mockResolvedValue({
        br_id: 'br1',
        status: 'ingesting',
        progress_pct: 0,
        error_text: null,
      })

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByTestId('refresh-btn')).toBeInTheDocument())

      fireEvent.click(screen.getByTestId('refresh-btn'))

      await waitFor(() => expect(vi.mocked(api.refreshBr)).toHaveBeenCalledWith('br1'))
      await waitFor(() => expect(screen.getByTestId('ingest-progress')).toBeInTheDocument())
    })
  })

  // -------------------------------------------------------------------------
  // Delete (FC / High Command)
  // -------------------------------------------------------------------------

  describe('delete button', () => {
    function renderWithHome() {
      return render(
        <MemoryRouter
          initialEntries={['/brs/br1']}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <Routes>
            <Route path="/brs/:id" element={<BrDetailPage />} />
            <Route path="/" element={<div>overview-home</div>} />
          </Routes>
        </MemoryRouter>
      )
    }

    it('non-creator does not see the Delete button', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)

      renderBrDetailPage()
      await waitFor(() => expect(screen.getByText('Test BR')).toBeInTheDocument())
      expect(screen.queryByTestId('delete-br-btn')).not.toBeInTheDocument()
    })

    it('confirmed delete calls api.deleteBr and navigates to the overview', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      vi.mocked(api.deleteBr).mockResolvedValue(undefined)
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

      renderWithHome()
      await waitFor(() => expect(screen.getByTestId('delete-br-btn')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('delete-br-btn'))

      await waitFor(() => expect(vi.mocked(api.deleteBr)).toHaveBeenCalledWith('br1'))
      await waitFor(() => expect(screen.getByText('overview-home')).toBeInTheDocument())
      confirmSpy.mockRestore()
    })

    it('cancelled confirm does not delete', async () => {
      vi.mocked(api.getBr).mockResolvedValue(mockBr)
      vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
      vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
      vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)
      vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

      renderWithHome()
      await waitFor(() => expect(screen.getByTestId('delete-br-btn')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('delete-br-btn'))

      expect(vi.mocked(api.deleteBr)).not.toHaveBeenCalled()
      expect(screen.queryByText('overview-home')).not.toBeInTheDocument()
      confirmSpy.mockRestore()
    })
  })

  it('shows the Battle (UTC) datetime in the summary header', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(false))
    vi.mocked(api.myBrCoverage).mockRejectedValue(new ApiError(404, 'none'))
    vi.mocked(api.fleetTimeline).mockResolvedValue(emptyFleet)
    renderBrDetailPage()
    const summary = await screen.findByTestId('summary-section')
    expect(within(summary).getByText('Battle (UTC)')).toBeInTheDocument()
    expect(within(summary).getByText('2026-06-10 18:00')).toBeInTheDocument()
  })
})
