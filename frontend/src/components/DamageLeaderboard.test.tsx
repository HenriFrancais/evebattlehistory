import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DamageLeaderboard } from './DamageLeaderboard'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      damageLeaderboard: vi.fn(),
    },
  }
})

import { api } from '../api'
import type { BrDamageLeaderboard } from '../api'

const mockLeaderboard: BrDamageLeaderboard = {
  rows: [
    { character_id: 1, character_name: 'TopGunner', damage_done: 50000, share: 0.5, log_damage_out: null },
    { character_id: 2, character_name: 'SecondDps', damage_done: 30000, share: 0.3, log_damage_out: null },
    { character_id: 3, character_name: 'ThirdDps', damage_done: 20000, share: 0.2, log_damage_out: null },
  ],
  total_attributed: 100000,
  logs_present: false,
}

describe('DamageLeaderboard', () => {
  beforeEach(() => {
    vi.mocked(api.damageLeaderboard).mockReset()
  })

  it('renders rows sorted by damage with share %', async () => {
    vi.mocked(api.damageLeaderboard).mockResolvedValue(mockLeaderboard)
    render(<DamageLeaderboard brId="br1" />)

    await waitFor(() => {
      expect(screen.getByTestId('damage-leaderboard')).toBeInTheDocument()
    })

    const rows = screen.getAllByTestId('dmg-lb-row')
    expect(rows).toHaveLength(3)

    // First row: TopGunner with 50% share
    expect(rows[0]).toHaveTextContent('TopGunner')
    expect(rows[0]).toHaveTextContent('50.0%')

    // Second row: SecondDps with 30% share
    expect(rows[1]).toHaveTextContent('SecondDps')
    expect(rows[1]).toHaveTextContent('30.0%')

    // Third row
    expect(rows[2]).toHaveTextContent('ThirdDps')
    expect(rows[2]).toHaveTextContent('20.0%')
  })

  it('calls api.damageLeaderboard with the brId', async () => {
    vi.mocked(api.damageLeaderboard).mockResolvedValue(mockLeaderboard)
    render(<DamageLeaderboard brId="br-test-123" />)

    await waitFor(() => {
      expect(vi.mocked(api.damageLeaderboard)).toHaveBeenCalledWith('br-test-123')
    })
  })

  it('shows loading state initially', () => {
    vi.mocked(api.damageLeaderboard).mockReturnValue(new Promise(() => {}))
    render(<DamageLeaderboard brId="br1" />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows error when fetch fails', async () => {
    vi.mocked(api.damageLeaderboard).mockRejectedValue(new Error('Network error'))
    render(<DamageLeaderboard brId="br1" />)

    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeInTheDocument()
    })
  })
})
