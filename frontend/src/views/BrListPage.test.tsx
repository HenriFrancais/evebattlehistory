import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrListResponse, BrSummary, MeResponse } from '../api'
import { BrListPage } from './BrListPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      me: vi.fn(),
      listBrs: vi.fn(),
    },
  }
})

import { api } from '../api'

const mockMe = (can: boolean): MeResponse => ({
  user_name: 'TestUser',
  user_rank: 'FC',
  user_teams: [],
  main_character_id: '12345',
  can_create_br: can,
})

const mockBr: BrSummary = {
  br_id: 'br1',
  title: 'Test Battle',
  source: 'zkillboard',
  source_url: 'https://zkillboard.com/related/30000142/202606101800/',
  status: 'ready',
  progress_pct: 100,
  result: 'win',
  isk_efficiency: 0.8,
  our_isk_destroyed: 1_000_000_000,
  our_isk_lost: 500_000_000,
  fight_count: 2,
  battle_at: '2026-06-10T18:00:00Z',
  created_at: '2026-06-10T20:00:00Z',
}

const mockList: BrListResponse = {
  summary: { total: 1, wins: 1, ties: 0, losses: 0, win_rate: 1.0, total_isk_destroyed: 1e9, total_isk_lost: 5e8 },
  brs: [mockBr],
}

describe('BrListPage', () => {
  beforeEach(() => {
    vi.mocked(api.listBrs).mockResolvedValue(mockList)
  })

  it('shows New Battle Report button when can_create_br is true', async () => {
    vi.mocked(api.me).mockResolvedValue(mockMe(true))
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrListPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByTestId('new-br-btn')).toBeInTheDocument())
  })

  it('hides New Battle Report button when can_create_br is false', async () => {
    vi.mocked(api.me).mockResolvedValue(mockMe(false))
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrListPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('Test Battle')).toBeInTheDocument())
    expect(screen.queryByTestId('new-br-btn')).not.toBeInTheDocument()
  })
})
