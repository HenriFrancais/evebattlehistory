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
    { side_kind: 'friendly', pilot_count: 2, ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 2, top_modules: [{ type_id: 3057, name: 'Heavy Pulse Laser II', role: 'turret' }, { type_id: 2048, name: 'Damage Control II', role: 'other' }] }],
      pilots: [
        { character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: false, killmail_id: null, user_name: null, weapons: [] },
        { character_id: 2, character_name: 'B', ship_type_id: 22428, ship_name: 'Absolution', lost: true, reship: false, killmail_id: 100, user_name: null, weapons: [] },
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

  it('renders top-module icons per hull with the module name as tooltip', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByText(/Absolution/)).toBeInTheDocument())
    const icon = screen.getByTitle('Heavy Pulse Laser II') as HTMLImageElement
    expect(icon.src).toContain('https://images.evetech.net/types/3057/icon')
    expect(screen.getByTitle('Damage Control II')).toBeInTheDocument()
    // 5 fixed columns (2 real + 3 empty placeholders) for alignment.
    expect(screen.getByTestId('ship-modules').children.length).toBe(5)
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
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1, top_modules: [] },
                { ship_type_id: 11987, ship_name: 'Guardian', count: 1, top_modules: [] }],
        pilots: [
          { character_id: 1, character_name: 'Talun', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: true, killmail_id: null, user_name: null, weapons: [] },
          { character_id: 1, character_name: 'Talun', ship_type_id: 11987, ship_name: 'Guardian', lost: false, reship: true, killmail_id: null, user_name: null, weapons: [] },
        ] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))
    expect(screen.getAllByText(/reship/i).length).toBeGreaterThanOrEqual(1)
  })

  it('shows expand toggle in per-character view and nested module rows on expand', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1, top_modules: [] }],
        pilots: [{ character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution',
                   lost: false, reship: false, killmail_id: null, user_name: null,
                   weapons: [
                     { type_id: 3074, name: 'Electron Blaster Cannon I', role: 'turret' },
                     { type_id: 2185, name: 'Hammerhead II', role: 'drone' },
                   ] }] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))

    // Toggle button is visible; modules collapsed by default
    const toggleBtn = screen.getByTestId('toggle-modules-btn')
    expect(toggleBtn).toBeInTheDocument()
    expect(screen.queryByTestId('pilot-modules')).not.toBeInTheDocument()

    // Expand modules
    await user.click(toggleBtn)
    const moduleRows = screen.getAllByTestId('module-row')
    expect(moduleRows).toHaveLength(2)

    // Each row has an icon img (alt="" → role=presentation) and the item name
    const itemIcons = document.querySelectorAll('img.comp-item-icon')
    expect(itemIcons.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Electron Blaster Cannon I')).toBeInTheDocument()
    expect(screen.getByText('Hammerhead II')).toBeInTheDocument()

    // Collapse again
    await user.click(toggleBtn)
    expect(screen.queryByTestId('pilot-modules')).not.toBeInTheDocument()
  })

  it('links a lost pilot ship to zKillboard', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 645, ship_name: 'Dominix', count: 1, top_modules: [] }],
        pilots: [{ character_id: 7, character_name: 'Vic', ship_type_id: 645, ship_name: 'Dominix',
                   lost: true, reship: false, user_name: null, killmail_id: 12345, weapons: [] }] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Per-character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /Per-character/i }))
    const link = screen.getByRole('link', { name: /lost/i })
    expect(link).toHaveAttribute('href', 'https://zkillboard.com/kill/12345/')
  })
})
