import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse } from '../api'
import { SnapshotPanel } from './SnapshotPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, snapshot: vi.fn() } }
})
import { api } from '../api'

const resp: ContributionsResponse = {
  from_ts: 1000, to_ts: 1010,
  rows: [
    // Loki target: 2 source rows → busiest, must sort first
    { source_character_id: 1, source_name: 'Talun', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 900,
      module_name: '250mm Railgun II', icon_type_id: 3174, weapon_category: 'hybrid', quality: 'Smashes' },
    { source_character_id: 2, source_name: 'Aiden', target_name: 'Crash', target_ship: 'Loki',
      effect_type: 'damage', direction: 'out', group: 'damage', value: 100,
      module_name: 'Hammerhead II', icon_type_id: 2185, weapon_category: 'drone', quality: 'Penetrates' },
    // Nestor target: 1 source row → single-source, must sink to bottom
    { source_character_id: 3, source_name: 'Sera', target_name: 'Toni', target_ship: 'Nestor',
      effect_type: 'rep_armor', direction: 'in', group: 'damage', value: 8000,
      module_name: null, icon_type_id: null, weapon_category: null, quality: null },
  ],
}

describe('SnapshotPanel', () => {
  beforeEach(() => vi.mocked(api.snapshot).mockReset())

  it('hint when no range selected; no fetch', () => {
    render(<SnapshotPanel brId="br1" range={null} />)
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
    expect(api.snapshot).not.toHaveBeenCalled()
  })

  it('fetches the range and heads groups with Name (Ship), busiest first', async () => {
    vi.mocked(api.snapshot).mockResolvedValue(resp)
    render(<SnapshotPanel brId="br1" range={{ from: 1000, to: 1010 }} />)
    expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument()
    // Rows arrive after the 120ms debounce + resolved fetch, so poll for the heads.
    await waitFor(() => expect(screen.getAllByTestId('focus-card-head').length).toBeGreaterThan(0))
    expect(api.snapshot).toHaveBeenCalledWith('br1', 1000, 1010)
    const heads = screen.getAllByTestId('focus-card-head').map((e) => e.textContent)
    expect(heads[0]).toMatch(/Crash \(Loki\)/)        // busiest (2 rows) on top
    expect(heads[heads.length - 1]).toMatch(/Toni \(Nestor\)/)  // single-source at bottom
    // quality tag present for a damage row
    expect(screen.getByText(/Smashes/)).toBeInTheDocument()
    // weapon icon
    expect((screen.getByTitle('250mm Railgun II') as HTMLImageElement).src).toContain('/types/3174/')
  })
})
