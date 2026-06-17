/**
 * E1: CoverageMatrix tests — log-only participant badge and timeline link.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { UserCoverage } from '../api'
import { CoverageMatrix } from './CoverageMatrix'

function renderMatrix(coverage: UserCoverage[], brId?: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <CoverageMatrix coverage={coverage} brId={brId} />
    </MemoryRouter>,
  )
}

const logOnlyCoverage: UserCoverage[] = [
  {
    user_name: 'LogiUser',
    characters: [
      {
        character_id: 333,
        character_name: 'LogiChar',
        participated_fights: [1],
        covered: true,
        fights_covered: [1],
        fights_missing: [],
        on_killmail: false,
        has_logs: true,
      },
    ],
  },
]

const confirmedCoverage: UserCoverage[] = [
  {
    user_name: 'DpsUser',
    characters: [
      {
        character_id: 111,
        character_name: 'DpsChar',
        participated_fights: [1],
        covered: true,
        fights_covered: [1],
        fights_missing: [],
        on_killmail: true,
        has_logs: true,
      },
    ],
  },
]

const missingCoverage: UserCoverage[] = [
  {
    user_name: 'AbsentUser',
    characters: [
      {
        character_id: 222,
        character_name: 'AbsentChar',
        participated_fights: [1],
        covered: false,
        fights_covered: [],
        fights_missing: [1],
        on_killmail: true,
        has_logs: false,
      },
    ],
  },
]

describe('CoverageMatrix — E1 log-only participant', () => {
  it('shows "logs only" badge for a character with has_logs=true and on_killmail=false', () => {
    renderMatrix(logOnlyCoverage, 'br1')

    // Character name should be rendered
    expect(screen.getByText('LogiChar')).toBeInTheDocument()

    // Badge should appear
    const badge = screen.getByTestId('log-only-badge-333')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveTextContent('logs only')
    expect(badge).toHaveAttribute('title', 'Logs only — not on a killmail')
  })

  it('log-only character links to their per-character timeline', () => {
    renderMatrix(logOnlyCoverage, 'br1')

    const link = screen.getByRole('link', { name: 'LogiChar' })
    expect(link).toHaveAttribute('href', '/brs/br1/characters/333')
  })

  it('does NOT show "logs only" badge for a killmail participant (on_killmail=true)', () => {
    renderMatrix(confirmedCoverage, 'br1')

    expect(screen.getByText('DpsChar')).toBeInTheDocument()
    expect(screen.queryByTestId('log-only-badge-111')).not.toBeInTheDocument()
  })

  it('does NOT show "logs only" badge for missing coverage (has_logs=false)', () => {
    renderMatrix(missingCoverage, 'br1')

    expect(screen.getByText('AbsentChar')).toBeInTheDocument()
    expect(screen.queryByTestId('log-only-badge-222')).not.toBeInTheDocument()
  })

  it('shows log-only badge without brId (no links)', () => {
    // When brId is undefined, names are plain text but badge still shows
    renderMatrix(logOnlyCoverage)

    expect(screen.getByText('LogiChar')).toBeInTheDocument()
    expect(screen.getByTestId('log-only-badge-333')).toBeInTheDocument()
    // No link when brId is absent
    expect(screen.queryByRole('link', { name: 'LogiChar' })).not.toBeInTheDocument()
  })
})
