import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { BrSummary } from '../api'
import { BrCard } from './BrCard'

function makeBr(overrides: Partial<BrSummary> = {}): BrSummary {
  return {
    br_id: 'abc123',
    title: 'Test BR',
    source: 'zkillboard',
    source_url: 'https://zkillboard.com/related/30000142/202606101800/',
    status: 'ready',
    progress_pct: 100,
    result: 'win',
    isk_efficiency: 0.75,
    our_isk_destroyed: 3_000_000_000,
    our_isk_lost: 1_000_000_000,
    fight_count: 3,
    battle_at: '2026-06-10T18:00:00Z',
    created_at: '2026-06-10T20:00:00Z',
    ...overrides,
  }
}

describe('BrCard', () => {
  it('shows win result badge with correct class', () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrCard br={makeBr({ result: 'win' })} /></MemoryRouter>)
    const badge = screen.getByTestId('result-badge')
    expect(badge).toHaveTextContent('win')
    expect(badge).toHaveClass('badge-win')
  })

  it('shows loss result badge', () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrCard br={makeBr({ result: 'loss' })} /></MemoryRouter>)
    const badge = screen.getByTestId('result-badge')
    expect(badge).toHaveClass('badge-loss')
  })

  it('shows tie result badge', () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrCard br={makeBr({ result: 'tie' })} /></MemoryRouter>)
    const badge = screen.getByTestId('result-badge')
    expect(badge).toHaveClass('badge-tie')
  })

  it('renders ISK values', () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrCard br={makeBr()} /></MemoryRouter>)
    expect(screen.getByText(/3\.00B killed/)).toBeInTheDocument()
    expect(screen.getByText(/1\.00B lost/)).toBeInTheDocument()
  })

  it('renders fight count', () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><BrCard br={makeBr({ fight_count: 5 })} /></MemoryRouter>)
    expect(screen.getByTestId('fight-count')).toHaveTextContent('5 fights')
  })
})
