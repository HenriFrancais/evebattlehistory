import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { BrListSummary } from '../api'
import { WinRateSummary } from './WinRateSummary'

const baseSummary: BrListSummary = {
  total: 10,
  wins: 6,
  ties: 1,
  losses: 3,
  win_rate: 0.6,
  total_isk_destroyed: 5_000_000_000,
  total_isk_lost: 2_000_000_000,
}

describe('WinRateSummary', () => {
  it('renders win rate percentage', () => {
    render(<WinRateSummary summary={baseSummary} />)
    expect(screen.getByText('60.0%')).toBeInTheDocument()
  })

  it('renders W/T/L counts', () => {
    render(<WinRateSummary summary={baseSummary} />)
    expect(screen.getByText('6W')).toBeInTheDocument()
    expect(screen.getByText('1T')).toBeInTheDocument()
    expect(screen.getByText('3L')).toBeInTheDocument()
  })

  it('shows 0.0% win rate when total is 0', () => {
    const empty: BrListSummary = {
      total: 0, wins: 0, ties: 0, losses: 0,
      win_rate: 0, total_isk_destroyed: 0, total_isk_lost: 0,
    }
    render(<WinRateSummary summary={empty} />)
    expect(screen.getByText('0.0%')).toBeInTheDocument()
  })

  it('formats ISK in billions', () => {
    render(<WinRateSummary summary={baseSummary} />)
    expect(screen.getByText('5.00B')).toBeInTheDocument()
    expect(screen.getByText('2.00B')).toBeInTheDocument()
  })
})
