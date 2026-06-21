import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LossDetailPanel } from './LossDetailPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      lossDamage: vi.fn(),
      lossItems: vi.fn(),
    },
  }
})

import { api } from '../api'
import type { LossDamageAttribution, ItemLossBreakdown } from '../api'

const mockDamage: LossDamageAttribution = {
  killmail_id: 99,
  damage_taken: 51234,
  total_attributed: 51234,
  attackers: [
    { character_id: 1, character_name: 'AlphaStrike', damage_done: 30000, share: 0.585, final_blow: true },
    { character_id: 2, character_name: 'BetaGun', damage_done: 21234, share: 0.415, final_blow: false },
  ],
}

const mockItems: ItemLossBreakdown = {
  killmail_id: 99,
  slots: [
    {
      location: 'high',
      destroyed_qty: 1,
      dropped_qty: 0,
      value: null,
      items: [
        { type_id: 1001, name: 'Heavy Neutron Blaster II', location: 'high', qty_destroyed: 1, qty_dropped: 0 },
      ],
    },
    {
      location: 'cargo',
      destroyed_qty: 0,
      dropped_qty: 50,
      value: null,
      items: [
        { type_id: 2002, name: 'Antimatter Charge L', location: 'cargo', qty_destroyed: 0, qty_dropped: 50 },
      ],
    },
  ],
}

describe('LossDetailPanel', () => {
  beforeEach(() => {
    vi.mocked(api.lossDamage).mockReset()
    vi.mocked(api.lossItems).mockReset()
  })

  it('shows "absorbed N damage" summary', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br1" killmailId={99} />)

    await waitFor(() => {
      expect(screen.getByText(/absorbed 51,234 damage/i)).toBeInTheDocument()
    })
  })

  it('marks the final-blow attacker', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br1" killmailId={99} />)

    await waitFor(() => {
      expect(screen.getByTestId('final-blow-marker')).toBeInTheDocument()
    })
  })

  it('shows share % for each attacker', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br1" killmailId={99} />)

    await waitFor(() => {
      expect(screen.getByText('AlphaStrike')).toBeInTheDocument()
    })

    // 58.5% share for AlphaStrike
    expect(screen.getByText('58.5%')).toBeInTheDocument()
    // 41.5% for BetaGun
    expect(screen.getByText('41.5%')).toBeInTheDocument()
  })

  it('renders a destroyed item row', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br1" killmailId={99} />)

    await waitFor(() => {
      expect(screen.getByText('Heavy Neutron Blaster II')).toBeInTheDocument()
    })
  })

  it('renders a dropped item row', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br1" killmailId={99} />)

    await waitFor(() => {
      expect(screen.getByText('Antimatter Charge L')).toBeInTheDocument()
    })
  })

  it('calls fetchers with correct brId and killmailId', async () => {
    vi.mocked(api.lossDamage).mockResolvedValue(mockDamage)
    vi.mocked(api.lossItems).mockResolvedValue(mockItems)
    render(<LossDetailPanel brId="br-abc" killmailId={42} />)

    await waitFor(() => {
      expect(vi.mocked(api.lossDamage)).toHaveBeenCalledWith('br-abc', 42)
      expect(vi.mocked(api.lossItems)).toHaveBeenCalledWith('br-abc', 42)
    })
  })
})
