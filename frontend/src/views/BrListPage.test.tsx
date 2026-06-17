import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrListResponse, BrSummary, FilteredBrResponse, MeResponse } from '../api'
import { BrListPage } from './BrListPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      me: vi.fn(),
      listBrs: vi.fn(),
      filterBrs: vi.fn(),
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
  impersonation_available: false,
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

const filteredBr: BrSummary = {
  br_id: 'br2',
  title: 'Filtered Battle',
  source: 'zkillboard',
  source_url: null,
  status: 'ready',
  progress_pct: 100,
  result: 'win',
  isk_efficiency: 0.9,
  our_isk_destroyed: 2_000_000_000,
  our_isk_lost: 200_000_000,
  fight_count: 1,
  battle_at: '2026-06-11T18:00:00Z',
  created_at: '2026-06-11T20:00:00Z',
}

const mockList: BrListResponse = {
  summary: { total: 1, wins: 1, ties: 0, losses: 0, win_rate: 1.0, total_isk_destroyed: 1e9, total_isk_lost: 5e8 },
  brs: [mockBr],
}

const filteredResponse: FilteredBrResponse = {
  summary: { total: 1, wins: 1, ties: 0, losses: 0, win_rate: 1.0, total_isk_destroyed: 2e9, total_isk_lost: 2e8 },
  brs: [filteredBr],
}

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <BrListPage />
    </MemoryRouter>
  )
}

describe('BrListPage', () => {
  beforeEach(() => {
    vi.mocked(api.listBrs).mockResolvedValue(mockList)
    vi.mocked(api.me).mockResolvedValue(mockMe(true))
    vi.mocked(api.filterBrs).mockResolvedValue(filteredResponse)
  })

  it('shows New Battle Report button when can_create_br is true', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('new-br-btn')).toBeInTheDocument())
  })

  it('hides New Battle Report button when can_create_br is false', async () => {
    vi.mocked(api.me).mockResolvedValue(mockMe(false))
    renderPage()
    await waitFor(() => expect(screen.getByText('Test Battle')).toBeInTheDocument())
    expect(screen.queryByTestId('new-br-btn')).not.toBeInTheDocument()
  })

  it('filter: applying calls filterBrs and updates the list', async () => {
    renderPage()
    // Wait for page to load
    await waitFor(() => expect(screen.getByText('Test Battle')).toBeInTheDocument())

    // Fill in the filter value input so Apply builds a valid clause
    const valueInput = screen.getByTestId('filter-row-0-value')
    await userEvent.type(valueInput, '50')

    // Click Apply
    fireEvent.click(screen.getByTestId('filter-apply'))

    // Wait for filtered results to appear
    await waitFor(() => expect(screen.getByText('Filtered Battle')).toBeInTheDocument())

    // Verify filter count indicator
    expect(screen.getByTestId('filter-count')).toBeInTheDocument()
    // Original BR should no longer be shown
    expect(screen.queryByText('Test Battle')).not.toBeInTheDocument()
  })

  it('filter: clear restores full list', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('Test Battle')).toBeInTheDocument())

    // Apply a filter
    const valueInput = screen.getByTestId('filter-row-0-value')
    await userEvent.type(valueInput, '50')
    fireEvent.click(screen.getByTestId('filter-apply'))
    await waitFor(() => expect(screen.getByText('Filtered Battle')).toBeInTheDocument())

    // Clear filter via the "Clear filter" button
    fireEvent.click(screen.getByTestId('filter-clear-results'))

    // Original BR should be back
    await waitFor(() => expect(screen.getByText('Test Battle')).toBeInTheDocument())
    expect(screen.queryByText('Filtered Battle')).not.toBeInTheDocument()
    expect(screen.queryByTestId('filter-count')).not.toBeInTheDocument()
  })
})
