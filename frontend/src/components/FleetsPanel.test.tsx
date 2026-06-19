import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompositionResponse } from '../api'
import { FleetsPanel } from './FleetsPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, composition: vi.fn() } }
})
import { api } from '../api'

const base: CompositionResponse = {
  by_user_available: false,
  sides: [
    { side_kind: 'friendly', pilot_count: 2, ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 2 }],
      pilots: [
        { character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: false, user_name: null },
        { character_id: 2, character_name: 'B', ship_type_id: 22428, ship_name: 'Absolution', lost: true, reship: false, user_name: null },
      ] },
  ],
}

describe('FleetsPanel', () => {
  beforeEach(() => vi.mocked(api.composition).mockReset())

  it('renders composition counts by default', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByText(/Absolution/)).toBeInTheDocument())
    expect(screen.getByText(/2×/)).toBeInTheDocument()
  })

  it('hides the By-user tab when not available', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Composition/i })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /By user/i })).not.toBeInTheDocument()
  })

  it('shows the By-user tab when available', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      ...base, by_user_available: true,
      sides: [{ ...base.sides[0],
        pilots: base.sides[0].pilots.map((p) => ({ ...p, user_name: 'hfrench' })) }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By user/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By user/i }))
    expect(screen.getByText(/hfrench/)).toBeInTheDocument()
  })

  it('shows a reship badge on reshipped pilots in per-character', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1 },
                { ship_type_id: 11987, ship_name: 'Guardian', count: 1 }],
        pilots: [
          { character_id: 1, character_name: 'Talun', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: true, user_name: null },
          { character_id: 1, character_name: 'Talun', ship_type_id: 11987, ship_name: 'Guardian', lost: false, reship: true, user_name: null },
        ] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))
    expect(screen.getAllByText(/reship/i).length).toBeGreaterThanOrEqual(1)
  })
})
