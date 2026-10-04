import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrEntities, CompositionPilot, CompositionResponse } from '../../api'
import { ApiError } from '../../api'
import { resetCache } from '../../cache'
import { EntityContext, buildEntityIndex } from '../../entities'
import { groupByMain, sortPilots } from '../../involved'
import { InvolvedTab } from './InvolvedTab'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    api: { ...actual.api, composition: vi.fn(), myBrCoverage: vi.fn(), setParticipantSide: vi.fn() },
  }
})
import { api } from '../../api'

const ENTITIES: BrEntities = {
  characters: [
    { character_id: 1, name: 'Ada', corporation_id: 10, alliance_id: 20 },
    { character_id: 4, name: 'Di', corporation_id: 12, alliance_id: null },
  ],
  corporations: [
    { corporation_id: 10, name: 'Big Corp', ticker: 'BIG', alliance_id: 20 },
    { corporation_id: 11, name: 'Small Corp', ticker: 'SML', alliance_id: 20 },
    { corporation_id: 12, name: 'Loner Corp', ticker: 'LONE', alliance_id: null },
  ],
  alliances: [{ alliance_id: 20, name: 'Blue Alliance', ticker: 'BLUE' }],
  by_name: [],
}
const index = buildEntityIndex(ENTITIES)

const pilot = (id: number, name: string, o: Partial<CompositionPilot> = {}): CompositionPilot => ({
  character_id: id, character_name: name, ship_type_id: 100, ship_name: 'Loki', lost: false,
  reship: false, killmail_id: null, user_name: null, weapons: [], damage_done: 0, kill_count: 0,
  reps_out: 0, has_logs: false, corporation_id: 10, alliance_id: 20,
  ship_group: 'Strategic Cruiser', ship_rank: 20, ...o,
})

const FRIENDLY = {
  side_kind: 'friendly', pilot_count: 5, losses: 1, isk_lost: 250_000_000,
  ships: [{ ship_type_id: 100, ship_name: 'Loki', count: 3, top_modules: [{ type_id: 7, name: 'Gun', role: 'dps' }] }],
  pilots: [
    pilot(1, 'Ada', { damage_done: 500, kill_count: 2, has_logs: true, can_download_log: true, user_name: 'ada_main',
      weapons: [{ type_id: 7, name: '720mm Howitzer', role: 'dps' }, { type_id: 8, name: 'Warp Scrambler II', role: 'tackle' }] }),
    pilot(2, 'Bo', { damage_done: 900, kill_count: 3, lost: true, killmail_id: 555, user_name: 'ada_main' }),
    pilot(3, 'Cy', { reship: true, ship_type_id: 101, ship_name: 'Guardian', ship_group: 'Logistics', ship_rank: 23 }),
    pilot(3, 'Cy', { reship: true, ship_type_id: 102, ship_name: 'Naglfar', ship_group: 'Dreadnought', ship_rank: 4 }),
    pilot(4, 'Di', { corporation_id: 12, alliance_id: null, ship_type_id: 103, ship_name: 'Rifter',
      ship_group: 'Frigate', ship_rank: 44, lost: true }),
    pilot(5, 'Ed', { corporation_id: null, alliance_id: null, from_logs: true, ship_type_id: null,
      ship_name: 'Unknown', ship_group: null, ship_rank: 999 }),
  ],
}
const COMP: CompositionResponse = {
  by_user_available: false,
  sides: [FRIENDLY, { side_kind: 'hostile', pilot_count: 1, losses: 0, isk_lost: 0, ships: [],
    pilots: [pilot(9, 'Zed', { corporation_id: 12, alliance_id: null })] }],
}

function setup(comp: CompositionResponse = COMP) {
  vi.mocked(api.composition).mockResolvedValue(comp)
  return render(
    <MemoryRouter>
      <EntityContext.Provider value={index}>
        <InvolvedTab brId="br1" />
      </EntityContext.Provider>
    </MemoryRouter>,
  )
}
const rowNames = (side: HTMLElement) =>
  within(side).getAllByTestId(/^pilot-\d+$/).map((r) => r.querySelector('.pilot-name')?.firstChild?.textContent)

describe('sortPilots', () => {
  it('runs from the largest hull class to the smallest, unknown hulls last', () => {
    expect(sortPilots(FRIENDLY.pilots).map((p) => p.ship_name)).toEqual([
      'Naglfar', 'Loki', 'Loki', 'Guardian', 'Rifter', 'Unknown',
    ])
  })

  it('within the same hull, the bigger damage dealer comes first', () => {
    const lokis = sortPilots(FRIENDLY.pilots).filter((p) => p.ship_name === 'Loki')
    expect(lokis.map((p) => p.character_name)).toEqual(['Bo', 'Ada'])
  })
})

describe('groupByMain', () => {
  it('groups characters under their main, unmatched characters last', () => {
    const groups = groupByMain(FRIENDLY)
    expect(groups.map((g) => [g.user, g.known])).toEqual([['ada_main', true], ['Not on the roster', false]])
    expect(groups[0].pilots.map((p) => p.character_name)).toEqual(['Bo', 'Ada'])
  })
})

describe('InvolvedTab', () => {
  beforeEach(() => {
    resetCache()
    vi.mocked(api.composition).mockReset()
    vi.mocked(api.myBrCoverage).mockReset().mockRejectedValue(new ApiError(404, 'none'))
  })

  it('lists each side as one row per pilot, largest hull first, with the side totals', async () => {
    setup()
    const friendly = await screen.findByTestId('involved-friendly')
    expect(friendly).toHaveTextContent('5 pilots')
    expect(friendly).toHaveTextContent('1 lost')
    expect(friendly).toHaveTextContent('250.00M ISK')
    expect(rowNames(friendly)).toEqual(['Cy', 'Bo', 'Ada', 'Cy', 'Di', 'Ed'])
    expect(screen.getByTestId('involved-hostile')).toHaveTextContent('Zed')
  })

  it('shows corp and alliance tickers in line with every pilot', async () => {
    setup()
    const friendly = await screen.findByTestId('involved-friendly')
    expect(within(friendly).getByTestId('pilot-1')).toHaveTextContent('Ada [BIG] <BLUE>')
    expect(within(friendly).getByTestId('pilot-4')).toHaveTextContent('Di [LONE]')
  })

  it('marks a lost ship by colouring the row, with the ship linking to its killmail', async () => {
    setup()
    const friendly = await screen.findByTestId('involved-friendly')
    const bo = within(friendly).getByTestId('pilot-2')
    expect(bo).toHaveClass('lost')
    expect(within(bo).getByRole('link', { name: 'Loki (lost)' }))
      .toHaveAttribute('href', 'https://zkillboard.com/kill/555/')
    expect(bo).not.toHaveTextContent('✗')
    expect(within(friendly).getByTestId('pilot-1')).not.toHaveClass('lost')
    // Lost with no killmail id: still coloured, just not a link.
    expect(within(friendly).getByTestId('pilot-4')).toHaveClass('lost')
  })

  it('clicking anywhere on a lost row opens that loss on zKillboard', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    setup({ ...COMP, sides: [{ ...FRIENDLY, pilots: [
      pilot(2, 'Bo', { lost: true, killmail_id: 555, has_logs: true, can_download_log: true,
        weapons: [{ type_id: 7, name: '720mm Howitzer', role: 'dps' }] }),
      pilot(1, 'Ada'),
    ] }] })
    const friendly = await screen.findByTestId('involved-friendly')
    const bo = within(friendly).getByTestId('pilot-2')
    await userEvent.click(within(bo).getByTitle('720mm Howitzer'))
    expect(open).toHaveBeenCalledWith('https://zkillboard.com/kill/555/', '_blank', 'noopener,noreferrer')
    // The row's own links and buttons keep their job and do not also open zKillboard.
    open.mockClear()
    await userEvent.click(within(bo).getByRole('link', { name: /^Bo/ }))
    expect(open).not.toHaveBeenCalled()
    // A row that is not a loss does nothing on click.
    await userEvent.click(within(friendly).getByTestId('pilot-1'))
    expect(open).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('links a pilot with visible logs to the timeline, isolated', async () => {
    setup()
    await screen.findByTestId('involved-friendly')
    expect(screen.getByRole('link', { name: /^Ada/ })).toHaveAttribute('href', '/brs/br1/timeline?pilots=1')
    expect(screen.queryByRole('link', { name: /^Bo/ })).not.toBeInTheDocument()
  })

  it('shows modules in line as icons named on hover, and the toggle hides them', async () => {
    setup()
    await screen.findByTestId('involved-friendly')
    const mods = within(screen.getByTestId('pilot-1')).getByTestId('pilot-modules')
    expect(within(mods).getAllByRole('img').map((i) => i.getAttribute('title')))
      .toEqual(['720mm Howitzer', 'Warp Scrambler II'])
    expect(mods).not.toHaveTextContent('720mm Howitzer') // the name is the tooltip, not text
    await userEvent.click(screen.getByRole('checkbox', { name: 'Modules' }))
    expect(screen.queryByTestId('pilot-modules')).not.toBeInTheDocument()
  })

  it('offers the timeline link and log download only for pilots the viewer may open', async () => {
    setup({ ...COMP, sides: [{ ...FRIENDLY, pilots: [
      pilot(1, 'Ada', { has_logs: true, can_download_log: true }),
      pilot(2, 'Bo', { has_logs: true, can_download_log: false, reps_out: 1200 }),
    ] }] })
    const friendly = await screen.findByTestId('involved-friendly')
    expect(within(within(friendly).getByTestId('pilot-1')).getByRole('button', { name: 'log' })).toBeInTheDocument()
    const bo = within(friendly).getByTestId('pilot-2')
    expect(within(bo).queryByRole('button', { name: 'log' })).not.toBeInTheDocument()
    // The reps figure and log dot show, but the name is not a link: this viewer
    // may not isolate that pilot on the timeline.
    expect(within(bo).queryByRole('link', { name: /^Bo/ })).not.toBeInTheDocument()
    expect(bo).toHaveTextContent('1.2k')
    expect(within(within(friendly).getByTestId('pilot-1')).getByRole('link', { name: /^Ada/ }))
      .toHaveAttribute('href', '/brs/br1/timeline?pilots=1')
  })

  it('marks reships and from-logs pilots', async () => {
    setup()
    const friendly = await screen.findByTestId('involved-friendly')
    expect(within(friendly).getAllByText('reship')).toHaveLength(2)
    expect(within(friendly).getByTestId('from-logs-badge')).toBeInTheDocument()
  })

  it('shows the hull composition on request, with the unknown from-logs row', async () => {
    setup()
    await screen.findByTestId('involved-friendly')
    await userEvent.click(screen.getByRole('button', { name: 'Ships' }))
    const friendly = screen.getByTestId('involved-friendly')
    expect(friendly).toHaveTextContent('3×')
    expect(within(friendly).getByTitle('Gun')).toBeInTheDocument()
    expect(within(friendly).getByTestId('from-logs-unknown')).toHaveTextContent('1×')
    expect(screen.queryByRole('checkbox', { name: 'Modules' })).not.toBeInTheDocument()
  })

  it('offers grouping by main only when the roster mapping is available', async () => {
    setup()
    await screen.findByTestId('involved-friendly')
    expect(screen.queryByRole('button', { name: 'By main' })).not.toBeInTheDocument()
  })

  it('groups by main for FC / High Command', async () => {
    setup({ ...COMP, by_user_available: true })
    await screen.findByTestId('involved-friendly')
    await userEvent.click(screen.getByRole('button', { name: 'By main' }))
    const groups = within(screen.getByTestId('involved-friendly')).getAllByTestId('inv-main')
    expect(groups[0]).toHaveTextContent('ada_main')
    expect(rowNames(groups[0])).toEqual(['Bo', 'Ada'])
    expect(groups[1]).toHaveTextContent('Not on the roster')
  })

  it('lets an FC set the side of a from-logs pilot', async () => {
    vi.mocked(api.setParticipantSide).mockResolvedValue({ ok: true })
    setup({ ...COMP, by_user_available: true })
    await screen.findByTestId('involved-friendly')
    await userEvent.click(within(screen.getByTestId('side-set-5')).getByRole('button', { name: 'H' }))
    await waitFor(() => expect(api.setParticipantSide).toHaveBeenCalledWith('br1', 5, 'hostile'))
  })

  it('tells a member how many of their characters have logs', async () => {
    vi.mocked(api.myBrCoverage).mockResolvedValue({
      user_name: 'me',
      characters: [
        { character_id: 1, character_name: 'Ada', participated_fights: [1], covered: true, fights_covered: [1], fights_missing: [] },
        { character_id: 2, character_name: 'Bo', participated_fights: [1], covered: false, fights_covered: [], fights_missing: [1] },
      ],
    })
    setup()
    const strip = await screen.findByTestId('my-coverage')
    expect(strip).toHaveTextContent('1 of 2 of your characters in this battle have logs.')
    expect(strip).toHaveTextContent('Missing: Bo.')
    expect(within(strip).getByRole('link', { name: 'Upload logs' })).toHaveAttribute('href', '/logs')
  })

  it('says nothing about coverage when the viewer took no part', async () => {
    setup()
    await screen.findByTestId('involved-friendly')
    expect(screen.queryByTestId('my-coverage')).not.toBeInTheDocument()
  })

  it('shows the error when the composition fails to load', async () => {
    vi.mocked(api.composition).mockRejectedValue(new Error('boom'))
    render(<MemoryRouter><InvolvedTab brId="br1" /></MemoryRouter>)
    expect(await screen.findByTestId('involved-error')).toHaveTextContent('boom')
  })
})
