import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrDetail, FightEwar, FightReconcile } from '../api'
import { FightDetailPage } from './FightDetailPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getBr: vi.fn(),
      fightReconcile: vi.fn(),
      fightEwar: vi.fn(),
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
  fight_count: 1,
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

const mockReconcile: FightReconcile = {
  rows: [
    {
      character_id: 42,
      character_name: 'TestPilot',
      log_damage_out: 1000,
      log_damage_in: 500,
      km_damage_attributed: 800,
      delta: 200,
    },
  ],
  dps_series: [{ bucket_ts_epoch: 1234567890, sum_damage_out: 100 }],
}

const mockEwar: FightEwar = {
  ewar: [
    {
      character_id: 10,
      effect_type: 'webifier',
      direction: 'outgoing',
      event_count: 5,
      first_ts: '2026-06-10T18:00:00Z',
      last_ts: '2026-06-10T18:05:00Z',
    },
  ],
  cap: [],
  logi: [],
}

const emptyEwar: FightEwar = { ewar: [], cap: [], logi: [] }

function renderFightDetailPage() {
  return render(
    <MemoryRouter
      initialEntries={['/brs/br1/fights/1']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/brs/:id/fights/:fid" element={<FightDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('FightDetailPage', () => {
  beforeEach(() => {
    vi.mocked(api.getBr).mockReset()
    vi.mocked(api.fightReconcile).mockReset()
    vi.mocked(api.fightEwar).mockReset()
  })

  it('ReconcilePanel renders rows with positive delta highlighted', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.fightReconcile).mockResolvedValue(mockReconcile)
    vi.mocked(api.fightEwar).mockResolvedValue(emptyEwar)

    renderFightDetailPage()

    // Wait for reconcile panel to load
    await waitFor(() => expect(screen.getByTestId('reconcile-panel')).toBeInTheDocument())

    // Delta value "200" should be visible
    expect(screen.getByText('200')).toBeInTheDocument()

    // The row with delta=200 should have class "delta-positive"
    const deltaPositiveRows = document.querySelectorAll('.delta-positive')
    expect(deltaPositiveRows.length).toBeGreaterThan(0)

    // DPS sparkline should be rendered
    expect(screen.getByTestId('dps-sparkline')).toBeInTheDocument()

    // Character name should be shown
    expect(screen.getByText('TestPilot')).toBeInTheDocument()
  })

  it('ReconcilePanel shows empty DPS state when no points', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.fightReconcile).mockResolvedValue({ rows: [], dps_series: [] })
    vi.mocked(api.fightEwar).mockResolvedValue(emptyEwar)

    renderFightDetailPage()

    await waitFor(() => expect(screen.getByTestId('reconcile-panel')).toBeInTheDocument())
    expect(screen.getByTestId('dps-sparkline')).toBeInTheDocument()
    expect(screen.getByText('No DPS data')).toBeInTheDocument()
  })

  it('EwarPanel renders ewar entries when data is present', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.fightReconcile).mockResolvedValue({ rows: [], dps_series: [] })
    vi.mocked(api.fightEwar).mockResolvedValue(mockEwar)

    renderFightDetailPage()

    await waitFor(() => expect(screen.getByTestId('ewar-panel')).toBeInTheDocument())
    expect(screen.getByText('webifier')).toBeInTheDocument()
  })

  it('EwarPanel shows friendly message when all sections empty', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.fightReconcile).mockResolvedValue({ rows: [], dps_series: [] })
    vi.mocked(api.fightEwar).mockResolvedValue(emptyEwar)

    renderFightDetailPage()

    await waitFor(() => expect(screen.getByTestId('ewar-panel')).toBeInTheDocument())
    expect(screen.getByText('No EWAR data')).toBeInTheDocument()
  })
})
