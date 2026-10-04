import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrDetail, BrSourceOut, MeResponse, UserCoverage } from '../api'
import { ApiError } from '../api'
import { loadFleetTimeline, resetCache } from '../cache'
import { BrDetailPage, CharacterRedirect } from './BrDetailPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  const names = [
    'getBr', 'me', 'myBrCoverage', 'brCoverage', 'fleetTimeline', 'pilotTimeline', 'composition',
    'entities', 'broadcasts', 'broadcastMetrics', 'broadcastFile', 'performance', 'getAar',
    'getSources', 'getSides', 'patchBrTitle', 'addSource', 'deleteSource', 'refreshBr', 'deleteBr',
    'getBrStatus', 'setParticipantSide',
  ] as const
  return { ...actual, api: { ...actual.api, ...Object.fromEntries(names.map((n) => [n, vi.fn()])) } }
})
// uPlot needs a canvas; the chart itself is not what these tests are about.
vi.mock('uplot', () => ({
  default: vi.fn().mockImplementation(() => ({ destroy: vi.fn(), setSize: vi.fn() })),
}))
import { api } from '../api'

const BR: BrDetail = {
  br_id: 'br1', title: 'Test BR', source: 'zkillboard', source_url: 'https://zkillboard.com/related/1/2/',
  status: 'ready', progress_pct: 100, result: 'win', isk_efficiency: 0.75,
  our_isk_destroyed: 1_000_000_000, our_isk_lost: 500_000_000, fight_count: 2,
  battle_at: '2026-06-10T18:00:00', created_at: '2026-06-10T20:00:00', systems: ['J125122'], fights: [],
}
const me = (can_create_br: boolean): MeResponse => ({
  user_name: 'TestUser', user_rank: 'FC', user_teams: [], main_character_id: '12345',
  can_create_br, impersonation_available: false,
})
const COVERAGE: UserCoverage[] = [{
  user_name: 'OtherUser',
  characters: [{ character_id: 222, character_name: 'BetaChar', participated_fights: [1], covered: false, fights_covered: [], fights_missing: [1] }],
}]
const SOURCE: BrSourceOut = {
  source_id: 7, br_id: 'br1', kind: 'window', url: null, system_id: 31000001, system_name: 'J125122',
  window_start: '2026-06-10T18:00:00', window_end: '2026-06-10T19:00:00', label: null,
  status: 'ready', error_text: null, km_count: 12,
}
const EMPTY_FLEET = { x: [], series: [], kills: [], fights: [], bucket_seconds: 5, t_start: null, t_end: null, leaders: [] }

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname + loc.search}</output>
}
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={<p>overview page</p>} />
        <Route path="/brs/:id" element={<BrDetailPage />} />
        <Route path="/brs/:id/characters/:charId" element={<CharacterRedirect />} />
        <Route path="/brs/:id/:tab" element={<BrDetailPage />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  )
}
const asFc = () => vi.mocked(api.me).mockResolvedValue(me(true))

describe('BrDetailPage', () => {
  beforeEach(() => {
    resetCache()
    for (const fn of Object.values(api)) if (vi.isMockFunction(fn)) fn.mockReset()
    vi.mocked(api.getBr).mockResolvedValue(BR)
    vi.mocked(api.me).mockResolvedValue(me(false))
    vi.mocked(api.myBrCoverage).mockRejectedValue(new ApiError(404, 'none'))
    vi.mocked(api.brCoverage).mockResolvedValue(COVERAGE)
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [
        { side_kind: 'friendly', pilot_count: 38, ships: [], pilots: [] },
        { side_kind: 'hostile', pilot_count: 52, ships: [], pilots: [] },
      ],
    })
    vi.mocked(api.entities).mockResolvedValue({ characters: [], corporations: [], alliances: [], by_name: [] })
    vi.mocked(api.fleetTimeline).mockResolvedValue(EMPTY_FLEET)
    vi.mocked(api.pilotTimeline).mockResolvedValue({ x: [], bucket_seconds: 5, pilots: [], scope: 'own' })
    vi.mocked(api.broadcasts).mockResolvedValue([])
    vi.mocked(api.broadcastMetrics).mockRejectedValue(new ApiError(404, 'none'))
    vi.mocked(api.getSources).mockResolvedValue([SOURCE])
    vi.mocked(api.getSides).mockResolvedValue({ entities: [], can_edit: true })
    vi.mocked(api.getBrStatus).mockResolvedValue({ br_id: 'br1', status: 'ready', progress_pct: 100, error_text: null })
    vi.mocked(api.getAar).mockRejectedValue(new Error('not under test'))
    vi.mocked(api.performance).mockRejectedValue(new Error('not under test'))
    vi.mocked(api.broadcastFile).mockResolvedValue(null)
  })

  describe('header', () => {
    it('shows title, system, battle time in UTC and the outcome', async () => {
      renderAt('/brs/br1')
      const header = await screen.findByTestId('summary-section')
      expect(within(header).getByRole('heading', { name: 'Test BR' })).toBeInTheDocument()
      expect(header).toHaveTextContent('J125122')
      expect(within(header).getByTestId('battle-time')).toHaveTextContent('2026-06-10 18:00 UTC')
      expect(header).toHaveTextContent('win')
      expect(header).toHaveTextContent('75.0%')
      expect(header).toHaveTextContent('1.00B')
      expect(header).toHaveTextContent('500.00M')
    })

    it('shows pilot counts per side once the composition has loaded', async () => {
      renderAt('/brs/br1')
      expect(await screen.findByTestId('pilot-counts')).toHaveTextContent('38 v 52')
    })

    it('links the source only when it is a web link', async () => {
      vi.mocked(api.getBr).mockResolvedValue({ ...BR, source_url: 'javascript:alert(1)' })
      renderAt('/brs/br1')
      const header = await screen.findByTestId('summary-section')
      expect(within(header).queryByRole('link', { name: /zkillboard/ })).not.toBeInTheDocument()
    })

    it('shows the ingest warning when killmails are missing', async () => {
      vi.mocked(api.getBr).mockResolvedValue({ ...BR, warning_text: 'Only 4 of 5 killmails could be fetched.' })
      renderAt('/brs/br1')
      expect(await screen.findByTestId('ingest-warning')).toHaveTextContent('Only 4 of 5 killmails')
    })

    it('shows no ingest warning for a complete report', async () => {
      renderAt('/brs/br1')
      await screen.findByTestId('summary-section')
      expect(screen.queryByTestId('ingest-warning')).not.toBeInTheDocument()
    })

    it('lets FC / High Command rename the report', async () => {
      asFc()
      vi.mocked(api.patchBrTitle).mockResolvedValue({ ...BR, title: 'Renamed' })
      renderAt('/brs/br1')
      await userEvent.click(await screen.findByTestId('edit-title-btn'))
      const input = screen.getByTestId('title-input')
      await userEvent.clear(input)
      await userEvent.type(input, 'Renamed')
      await userEvent.click(screen.getByTestId('save-title-btn'))
      await waitFor(() => expect(api.patchBrTitle).toHaveBeenCalledWith('br1', 'Renamed'))
      expect(await screen.findByRole('heading', { name: 'Renamed' })).toBeInTheDocument()
    })

    it('gives a member no edit button', async () => {
      renderAt('/brs/br1')
      await screen.findByTestId('summary-section')
      expect(screen.queryByTestId('edit-title-btn')).not.toBeInTheDocument()
    })
  })

  describe('tabs', () => {
    it('opens on Involved', async () => {
      renderAt('/brs/br1')
      expect(await screen.findByTestId('involved-tab')).toBeInTheDocument()
      expect(screen.getByTestId('tab-involved')).toHaveAttribute('aria-current', 'page')
      expect(screen.queryByTestId('timeline-tab')).not.toBeInTheDocument()
    })

    it('renders the tab named in the URL', async () => {
      renderAt('/brs/br1/timeline')
      expect(await screen.findByTestId('timeline-empty')).toBeInTheDocument()
      expect(screen.getByTestId('tab-timeline')).toHaveAttribute('aria-current', 'page')
    })

    it('switches tab from the tab bar and updates the URL', async () => {
      renderAt('/brs/br1')
      await userEvent.click(await screen.findByTestId('tab-performance'))
      expect(await screen.findByTestId('performance-tab')).toBeInTheDocument()
      expect(screen.getByTestId('where')).toHaveTextContent('/brs/br1/performance')
    })

    it('falls back to Involved for an unknown tab', async () => {
      renderAt('/brs/br1/nonsense')
      expect(await screen.findByTestId('involved-tab')).toBeInTheDocument()
    })

    it('hides Manage from a member, including by direct URL', async () => {
      renderAt('/brs/br1/manage')
      expect(await screen.findByTestId('involved-tab')).toBeInTheDocument()
      expect(screen.queryByTestId('tab-manage')).not.toBeInTheDocument()
      expect(screen.queryByTestId('manage-tab')).not.toBeInTheDocument()
      expect(api.getSources).not.toHaveBeenCalled()
    })

    it('redirects the old per-character URL to the timeline with that pilot isolated', async () => {
      renderAt('/brs/br1/characters/5')
      await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/brs/br1/timeline?pilots=5'))
    })
  })

  describe('manage tab (FC / High Command)', () => {
    beforeEach(asFc)

    it('lists sources by system name with status and killmail count', async () => {
      renderAt('/brs/br1/manage')
      const panel = await screen.findByTestId('sources-panel')
      await waitFor(() => expect(panel).toHaveTextContent('J125122'))
      expect(panel).toHaveTextContent('ready')
      expect(panel).toHaveTextContent('12 km')
    })

    it('shows the full coverage matrix, with names linking to the timeline', async () => {
      renderAt('/brs/br1/manage')
      const section = await screen.findByTestId('log-coverage-section')
      const link = await within(section).findByRole('link', { name: 'BetaChar' })
      expect(link).toHaveAttribute('href', '/brs/br1/timeline?pilots=222')
      expect(section).toHaveTextContent('OtherUser')
    })

    it('deleting a source refreshes the report', async () => {
      vi.mocked(api.deleteSource).mockResolvedValue(undefined)
      vi.mocked(api.refreshBr).mockResolvedValue({ br_id: 'br1', status: 'ready', progress_pct: 100, error_text: null })
      renderAt('/brs/br1/manage')
      await userEvent.click(await screen.findByTestId('delete-source-7'))
      await waitFor(() => expect(api.deleteSource).toHaveBeenCalledWith('br1', 7))
      await waitFor(() => expect(api.refreshBr).toHaveBeenCalledWith('br1'))
    })

    it('Refresh starts a refresh and shows its progress', async () => {
      vi.mocked(api.refreshBr).mockResolvedValue({ br_id: 'br1', status: 'fetching', progress_pct: 10, error_text: null })
      vi.mocked(api.getBrStatus).mockResolvedValue({ br_id: 'br1', status: 'fetching', progress_pct: 10, error_text: null })
      renderAt('/brs/br1/manage')
      await userEvent.click(await screen.findByTestId('refresh-btn'))
      await waitFor(() => expect(api.refreshBr).toHaveBeenCalledWith('br1'))
      expect(await screen.findByTestId('ingest-progress')).toHaveTextContent('fetching')
    })

    it('a confirmed delete removes the report and returns to the overview', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      vi.mocked(api.deleteBr).mockResolvedValue(undefined)
      renderAt('/brs/br1/manage')
      await userEvent.click(await screen.findByTestId('delete-br-btn'))
      await waitFor(() => expect(api.deleteBr).toHaveBeenCalledWith('br1'))
      expect(await screen.findByText('overview page')).toBeInTheDocument()
    })

    it('a cancelled delete does nothing', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(false)
      renderAt('/brs/br1/manage')
      await userEvent.click(await screen.findByTestId('delete-br-btn'))
      expect(api.deleteBr).not.toHaveBeenCalled()
    })
  })

  it('a side change makes the timeline refetch instead of reusing prefetched data', async () => {
    asFc()
    vi.mocked(api.setParticipantSide).mockResolvedValue({ ok: true })
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: true,
      sides: [{
        side_kind: 'friendly', pilot_count: 1, ships: [],
        pilots: [{
          character_id: 5, character_name: 'Ed', ship_type_id: null, ship_name: 'Unknown', lost: false,
          reship: false, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0,
          reps_out: 0, has_logs: false, from_logs: true,
        }],
      }],
    })
    await loadFleetTimeline('br1') // as the overview's hover prefetch would
    expect(api.fleetTimeline).toHaveBeenCalledTimes(1)
    renderAt('/brs/br1')
    await userEvent.click(within(await screen.findByTestId('side-set-5')).getByRole('button', { name: 'H' }))
    await waitFor(() => expect(api.setParticipantSide).toHaveBeenCalled())
    await userEvent.click(screen.getByTestId('tab-timeline'))
    await screen.findByTestId('timeline-empty')
    expect(api.fleetTimeline).toHaveBeenCalledTimes(2)
  })

  it('shows the error when the report cannot be loaded', async () => {
    vi.mocked(api.getBr).mockRejectedValue(new Error('not found'))
    renderAt('/brs/br1')
    expect(await screen.findByRole('alert')).toHaveTextContent('not found')
  })
})
