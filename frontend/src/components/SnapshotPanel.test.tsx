import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse } from '../api'
import { SnapshotPanel } from './SnapshotPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, snapshot: vi.fn(), characterSnapshot: vi.fn() } }
})
import { api } from '../api'

const resp: ContributionsResponse = {
  from_ts: 1000, to_ts: 1010,
  rows: [
    // Two damage sources onto Crash (Loki) — same target, damage family.
    { source_character_id: 1, source_name: 'Talun', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 900,
      module_name: '250mm Railgun II', icon_type_id: 3174, weapon_category: 'hybrid', quality: 'Smashes' },
    { source_character_id: 2, source_name: 'Aiden', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 100,
      module_name: 'Hammerhead II', icon_type_id: 2185, weapon_category: 'drone', quality: 'Penetrates' },
    // Incoming rep: owner 'Sera' RECEIVED armor rep from Toni → target flips to Sera, reps family.
    { source_character_id: 3, source_name: 'Sera', target_name: 'Toni', target_ship: 'Nestor',
      effect_type: 'rep_armor', direction: 'in', group: 'damage', value: 8000,
      module_name: null, icon_type_id: null, weapon_category: null, quality: null },
  ],
}

describe('SnapshotPanel', () => {
  beforeEach(() => {
    vi.mocked(api.snapshot).mockReset()
    vi.mocked(api.characterSnapshot).mockReset()
  })

  it('hint when no range selected; no fetch', () => {
    render(<SnapshotPanel brId="br1" range={null} />)
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
    expect(api.snapshot).not.toHaveBeenCalled()
  })

  it('groups by target (highest total first), nests effect families, drops direction', async () => {
    vi.mocked(api.snapshot).mockResolvedValue(resp)
    render(<SnapshotPanel brId="br1" range={{ from: 1000, to: 1010 }} />)
    expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('focus-card-head').length).toBeGreaterThan(0))
    expect(api.snapshot).toHaveBeenCalledWith('br1', 1000, 1010)

    const heads = screen.getAllByTestId('focus-card-head').map((e) => e.textContent)
    // Sera (8000, incoming reps) outweighs Crash (1000 damage).
    expect(heads[0]).toMatch(/^Sera$/)
    expect(heads[heads.length - 1]).toMatch(/Crash \(Loki\)/)

    // Crash's damage family lists both sources; quality + weapon icon present.
    expect(screen.getByText('Talun')).toBeInTheDocument()
    expect(screen.getByText('Aiden')).toBeInTheDocument()
    expect(screen.getByText(/Smashes/)).toBeInTheDocument()
    expect((screen.getByTitle('250mm Railgun II') as HTMLImageElement).src).toContain('/types/3174/')

    // Family sub-sections render (damage + reps), no others.
    expect(screen.getAllByTestId('fam-damage').length).toBe(1)
    expect(screen.getAllByTestId('fam-reps').length).toBe(1)
    expect(screen.queryByTestId('fam-cap')).not.toBeInTheDocument()

    // Incoming rep flipped: Toni is the source under Sera.
    expect(screen.getByText('Toni')).toBeInTheDocument()
  })

  it('uses the character-scoped endpoint when charId is given', async () => {
    vi.mocked(api.characterSnapshot).mockResolvedValue({ from_ts: 1000, to_ts: 1010, rows: [] })
    render(<SnapshotPanel brId="br1" charId="42" range={{ from: 1000, to: 1010 }} />)
    await waitFor(() => expect(api.characterSnapshot).toHaveBeenCalledWith('br1', '42', 1000, 1010))
    expect(api.snapshot).not.toHaveBeenCalled()
  })
})
