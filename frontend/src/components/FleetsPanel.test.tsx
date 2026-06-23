import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompositionResponse } from '../api'
import { FleetsPanel } from './FleetsPanel'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, composition: vi.fn(), searchShipTypes: vi.fn(), setParticipantShip: vi.fn() },
  }
})
import { api } from '../api'

const base: CompositionResponse = {
  by_user_available: false,
  sides: [
    { side_kind: 'friendly', pilot_count: 2, ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 2, top_modules: [{ type_id: 3057, name: 'Heavy Pulse Laser II', role: 'turret' }, { type_id: 2048, name: 'Damage Control II', role: 'other' }] }],
      pilots: [
        { character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: false, killmail_id: null, user_name: null, weapons: [], damage_done: 44000, kill_count: 4, reps_out: 12300, has_logs: true },
        { character_id: 2, character_name: 'B', ship_type_id: 22428, ship_name: 'Absolution', lost: true, reship: false, killmail_id: 100, user_name: null, weapons: [], damage_done: 1500, kill_count: 1, reps_out: 0, has_logs: false },
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

  it('shows per-pilot damage, killmail count, and reps in per-character view', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))

    // Char A: 44000 dmg → "44k", 4 unique killmails → "[4]", 12300 reps → "12.3k".
    expect(screen.getByText('44k')).toBeInTheDocument()
    expect(screen.getByText('[4]')).toBeInTheDocument()
    expect(screen.getByText('12.3k')).toBeInTheDocument()
    // Char B has zero reps → no rep figure rendered for it (only A's reps shown).
    expect(screen.getByText('1.5k')).toBeInTheDocument()
    expect(screen.getAllByText('12.3k')).toHaveLength(1)
  })

  it('shows a green/red logs-provided dot per friendly pilot in per-character view', async () => {
    vi.mocked(api.composition).mockResolvedValue(base)
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))

    // Char A uploaded logs → green dot; Char B did not → red dot.
    expect(screen.getByTitle('logs uploaded')).toHaveClass('comp-log-yes')
    expect(screen.getByTitle('no logs uploaded')).toHaveClass('comp-log-no')
  })

  it('hides the logs-provided dot for non-friendly sides', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      ...base,
      sides: [{ ...base.sides[0], side_kind: 'hostile' }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))

    expect(screen.queryByTitle('logs uploaded')).not.toBeInTheDocument()
    expect(screen.queryByTitle('no logs uploaded')).not.toBeInTheDocument()
  })

  it('shows a reship badge on reshipped pilots in per-character', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1, top_modules: [] },
                { ship_type_id: 11987, ship_name: 'Guardian', count: 1, top_modules: [] }],
        pilots: [
          { character_id: 1, character_name: 'Talun', ship_type_id: 22428, ship_name: 'Absolution', lost: false, reship: true, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0, reps_out: 0, has_logs: false },
          { character_id: 1, character_name: 'Talun', ship_type_id: 11987, ship_name: 'Guardian', lost: false, reship: true, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0, reps_out: 0, has_logs: false },
        ] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))
    expect(screen.getAllByText(/reship/i).length).toBeGreaterThanOrEqual(1)
  })

  it('shows expand toggle in per-character view and nested module rows on expand', async () => {
    vi.mocked(api.composition).mockResolvedValue({
      by_user_available: false,
      sides: [{ side_kind: 'friendly', pilot_count: 1,
        ships: [{ ship_type_id: 22428, ship_name: 'Absolution', count: 1, top_modules: [] }],
        pilots: [{ character_id: 1, character_name: 'A', ship_type_id: 22428, ship_name: 'Absolution',
                   lost: false, reship: false, killmail_id: null, user_name: null,
                   damage_done: 0, kill_count: 0, reps_out: 0, has_logs: false,
                   weapons: [
                     { type_id: 3074, name: 'Electron Blaster Cannon I', role: 'turret' },
                     { type_id: 2185, name: 'Hammerhead II', role: 'drone' },
                   ] }] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))

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
                   lost: true, reship: false, user_name: null, killmail_id: 12345, weapons: [],
                   damage_done: 0, kill_count: 0, reps_out: 0, has_logs: false }] }],
    })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))
    const link = screen.getByRole('link', { name: /lost/i })
    expect(link).toHaveAttribute('href', 'https://zkillboard.com/kill/12345/')
  })

  const withFromLogs: CompositionResponse = {
    by_user_available: true,
    sides: [
      { side_kind: 'friendly', pilot_count: 2,
        ships: [{ ship_type_id: 11987, ship_name: 'Guardian', count: 1, top_modules: [] }],
        pilots: [
          { character_id: 10, character_name: 'KnownLogi', ship_type_id: 11987, ship_name: 'Guardian', lost: false, reship: false, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0, reps_out: 5000, has_logs: true, from_logs: true },
          { character_id: 11, character_name: 'NoHull', ship_type_id: null, ship_name: 'Unknown', lost: false, reship: false, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0, reps_out: 0, has_logs: true, from_logs: true },
        ] },
    ],
  }

  it('marks from-logs pilots and shows the By-ship Unknown row', async () => {
    vi.mocked(api.composition).mockResolvedValue(withFromLogs)
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    // By-ship (composition) mode: the Unknown-from-logs count row.
    await waitFor(() => expect(screen.getByTestId('from-logs-unknown')).toBeInTheDocument())
    // By-character: the from-logs badge appears.
    await user.click(screen.getByRole('button', { name: /By character/i }))
    expect(screen.getAllByTestId('from-logs-badge').length).toBeGreaterThan(0)
  })

  it('lets an FC assign a ship to an Unknown from-logs pilot', async () => {
    vi.mocked(api.composition).mockResolvedValue(withFromLogs)
    vi.mocked(api.searchShipTypes).mockResolvedValue([{ type_id: 620, name: 'Osprey' }])
    vi.mocked(api.setParticipantShip).mockResolvedValue({ ok: true })
    const user = userEvent.setup()
    render(<FleetsPanel brId="br1" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /By character/i })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /By character/i }))
    // The Unknown pilot (character 11) shows a ship picker.
    await user.click(screen.getByTestId('ship-picker-11'))
    await user.type(screen.getByPlaceholderText('Search ship…'), 'osp')
    await user.click(await screen.findByText('Osprey'))
    expect(api.setParticipantShip).toHaveBeenCalledWith('br1', 11, 620)
  })
})
