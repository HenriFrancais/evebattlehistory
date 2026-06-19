import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse } from '../api'
import { MomentDetailPanel } from './MomentDetailPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, contributions: vi.fn() } }
})
import { api } from '../api'

const resp: ContributionsResponse = {
  at: 1000,
  bucket_seconds: 5,
  rows: [
    { source_character_id: 1, source_name: 'Talun', target_name: 'Loki', effect_type: 'damage',
      direction: 'out', group: 'damage', value: 9200, module_name: '250mm Railgun II',
      icon_type_id: 3174, weapon_category: 'hybrid' },
    { source_character_id: 2, source_name: 'Aiden', target_name: 'Nestor', effect_type: 'rep_armor',
      direction: 'in', group: 'damage', value: 8000, module_name: null, icon_type_id: null,
      weapon_category: null },
  ],
}

describe('MomentDetailPanel', () => {
  beforeEach(() => vi.mocked(api.contributions).mockReset())

  it('shows a hint when no moment is selected', () => {
    render(<MomentDetailPanel brId="br1" at={null} />)
    expect(screen.getByTestId('moment-detail-empty')).toBeInTheDocument()
    expect(api.contributions).not.toHaveBeenCalled()
  })

  it('renders a weapon icon for damage rows and effect icon for non-damage', async () => {
    vi.mocked(api.contributions).mockResolvedValue(resp)
    render(<MomentDetailPanel brId="br1" at={1000} />)
    expect(screen.getByTestId('fleet-contrib')).toBeInTheDocument()
    // Rows arrive after the 120ms debounce + resolved fetch, so poll for the icon.
    const weapon = (await screen.findByTitle('250mm Railgun II')) as HTMLImageElement
    expect(weapon.src).toContain('/types/3174/icon')
  })
})
