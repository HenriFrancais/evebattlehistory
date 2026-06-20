import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { BrSummary } from '../api'
import { BrTimelineTable } from './BrTimelineTable'

function makeBr(overrides: Partial<BrSummary> = {}): BrSummary {
  return {
    br_id: 'b1',
    title: 'June Brawl',
    source: 'zkillboard',
    source_url: null,
    status: 'ready',
    progress_pct: 100,
    result: 'win',
    isk_efficiency: 0.8,
    our_isk_destroyed: 3_000_000_000,
    our_isk_lost: 1_000_000_000,
    fight_count: 2,
    battle_at: '2026-06-10T18:30:00Z',
    created_at: '2026-06-10T20:00:00Z',
    systems: ['J123456'],
    our_name: 'No Vacancies.',
    opponent_name: 'Big Enemy',
    friendly_pilots: 12,
    enemy_pilots: 18,
    you_present: true,
    your_present: 2,
    your_logged: 1,
    roster_present: 20,
    roster_logged: 12,
    ...overrides,
  }
}

function renderTable(brs: BrSummary[]) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <BrTimelineTable brs={brs} />
    </MemoryRouter>,
  )
}

describe('BrTimelineTable', () => {
  it('renders a row with entities, pilots, presence and coverage', () => {
    renderTable([makeBr()])
    const row = screen.getByTestId('timeline-row')
    expect(within(row).getByText('June Brawl')).toBeInTheDocument()
    expect(within(row).getByText('No Vacancies.')).toBeInTheDocument()
    expect(within(row).getByText('Big Enemy')).toBeInTheDocument()
    expect(within(row).getByText('J123456')).toBeInTheDocument()
    expect(within(row).getByText('3.00B')).toBeInTheDocument()
    expect(within(row).getByText('1.00B')).toBeInTheDocument()
    // pilot counts
    expect(within(row).getByText('12')).toBeInTheDocument()
    expect(within(row).getByText('18')).toBeInTheDocument()
    // coverage fractions, your (1/2) + roster (12/20)
    expect(within(row).getByText('1/2')).toBeInTheDocument()
    expect(within(row).getByText('12/20')).toBeInTheDocument()
  })

  it('groups rows into year-month sections, newest first', () => {
    const june = makeBr({ br_id: 'b1', title: 'June', battle_at: '2026-06-10T18:00:00Z' })
    const may = makeBr({ br_id: 'b2', title: 'May', battle_at: '2026-05-02T18:00:00Z' })
    renderTable([may, june])
    const heads = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(heads).toEqual(['2026-06', '2026-05'])
  })

  it('marks absence when you were not present', () => {
    renderTable([makeBr({ you_present: false })])
    const row = screen.getByTestId('timeline-row')
    expect(within(row).getByTitle('You were not present')).toBeInTheDocument()
  })

  it('shows empty state when there are no battle reports', () => {
    renderTable([])
    expect(screen.getByText('No battle reports yet.')).toBeInTheDocument()
  })
})
