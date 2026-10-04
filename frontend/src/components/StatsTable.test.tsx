import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContributionsResponse, PilotTimelineRow } from '../api'
import type { PilotStatRow } from '../pilotStats'
import { breakdownRows } from './PilotBreakdown'
import { StatsTable } from './StatsTable'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, characterSnapshot: vi.fn() } }
})
import { api } from '../api'

const pilot = (id: number, name: string, ship: string): PilotTimelineRow => ({
  character_id: id, character_name: name, ship_type_id: null, ship_name: ship,
  side_kind: 'friendly', is_self: false, series: [],
})
const PILOTS = [pilot(1, 'Ada', 'Loki'), pilot(2, 'Bo', 'Guardian'), pilot(3, 'Cy', 'Zealot')]
const row = (id: number, o: Partial<PilotStatRow>): PilotStatRow => ({
  characterId: id, total: 0, peak: 0, avgPerSec: 0, min: null, max: null, hits: 0, misses: 0, ...o,
})
const ROWS = [
  row(1, { total: 4500, peak: 300, avgPerSec: 45, min: 40, max: 1500, hits: 12, misses: 3 }),
  row(2, { total: 9000, peak: 600, avgPerSec: 90, min: 80, max: 700, hits: 30, misses: 1 }),
  row(3, {}),
]
const TOTALS = row(0, { total: 13500, peak: 800, avgPerSec: 135, min: 40, max: 1500, hits: 42, misses: 4 })

function setup(over: Partial<Parameters<typeof StatsTable>[0]> = {}) {
  const onToggle = vi.fn()
  render(
    <StatsTable
      brId="br1" rows={ROWS} totals={TOTALS} pilots={PILOTS} family="damage" direction="out"
      selected={new Set()} onToggle={onToggle} range={{ from: 1000, to: 1100 }} scope="all" {...over}
    />,
  )
  return { onToggle }
}
const names = () =>
  screen.getAllByTestId(/^stats-row-/).map((r) => within(r).getByText(/Ada|Bo|Cy/).textContent)

describe('StatsTable', () => {
  beforeEach(() => vi.mocked(api.characterSnapshot).mockReset())

  it('sorts by total, highest first, by default', () => {
    setup()
    expect(names()).toEqual(['Bo', 'Ada'])
  })

  it('sorts by a clicked column and reverses on a second click', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: /^Max/ }))
    expect(names()).toEqual(['Ada', 'Bo'])
    await userEvent.click(screen.getByRole('button', { name: /^Max/ }))
    expect(names()).toEqual(['Bo', 'Ada'])
    await userEvent.click(screen.getByRole('button', { name: /^Pilot/ }))
    expect(names()).toEqual(['Ada', 'Bo'])
  })

  it('hides idle pilots until asked', async () => {
    setup()
    expect(screen.queryByTestId('stats-row-3')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show 1 idle pilot' }))
    expect(screen.getByTestId('stats-row-3')).toBeInTheDocument()
  })

  it('keeps a ticked pilot visible even when idle in the range', () => {
    setup({ selected: new Set([3]) })
    expect(screen.getByTestId('stats-row-3')).toBeInTheDocument()
  })

  it('ticking a row asks to isolate that pilot', async () => {
    const { onToggle } = setup()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Isolate Ada' }))
    expect(onToggle).toHaveBeenCalledWith(1)
  })

  it('shows a dash, not zero, for values that were not recorded', () => {
    setup({ rows: [row(1, { total: 500, hits: 2, min: null, max: null, misses: null })] })
    const cells = within(screen.getByTestId('stats-row-1')).getAllByLabelText('not recorded')
    expect(cells).toHaveLength(3) // min, max, misses
  })

  it('shows the totals row', () => {
    setup()
    const totals = screen.getByTestId('stats-totals')
    expect(totals).toHaveTextContent('All pilots')
    expect(totals).toHaveTextContent('13.5k')
  })

  it('reps count cycles and have no misses column', () => {
    setup({ family: 'reps' })
    expect(screen.getByRole('button', { name: /^Cycles/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Misses/ })).not.toBeInTheDocument()
  })

  it('EWAR shows only total and peak', () => {
    setup({ family: 'ewar' })
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toEqual(['Isolate', 'Pilot', 'Ship', 'Total', 'Peak'])
  })

  it('a member sees every pilot but can only isolate or open the breakdown of their own characters', () => {
    setup({ scope: 'own', pilots: [{ ...PILOTS[0], is_self: true }, PILOTS[1], PILOTS[2]] })
    expect(names()).toEqual(['Ada', 'Bo']) // both rows are listed, the viewer's own first
    expect(screen.getByRole('button', { name: 'Show breakdown for Ada' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show breakdown for Bo' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Isolate Ada' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Isolate Bo' })).not.toBeInTheDocument()
    expect(screen.getByTestId('stats-scope-note')).toHaveTextContent('your own characters')
  })

  it('FC / High Command can open every pilot\'s breakdown', () => {
    setup()
    expect(screen.getByRole('button', { name: 'Show breakdown for Bo' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Isolate Bo' })).toBeInTheDocument()
    expect(screen.queryByTestId('stats-scope-note')).not.toBeInTheDocument()
  })

  it('lists the viewer\'s own characters first, in their own section', () => {
    setup({ scope: 'own', pilots: [{ ...PILOTS[0], is_self: true }, PILOTS[1], PILOTS[2]] })
    const rows = screen.getAllByTestId(/^stats-(row|group)-/).map((r) => r.getAttribute('data-testid'))
    expect(rows).toEqual(['stats-group-own', 'stats-row-1', 'stats-group-others', 'stats-row-2'])
    expect(screen.getByTestId('stats-group-own')).toHaveTextContent('Your characters')
  })

  describe('grouped by user (FC / High Command)', () => {
    const OWNED = [
      { ...PILOTS[0], owner: 'Ada' }, { ...PILOTS[1], owner: 'Ada' }, { ...PILOTS[2], owner: 'Cy Main' },
      pilot(4, 'Dee', 'Onyx'),
    ]
    const OWNED_ROWS = [...ROWS.slice(0, 2), row(3, { total: 20000, hits: 5 }), row(4, { total: 10, hits: 1 })]
    const order = () =>
      screen.getAllByTestId(/^stats-(row|group)-/).map((r) => r.getAttribute('data-testid'))

    it('puts a user\'s characters under one heading, users ordered by their combined total', () => {
      setup({ pilots: OWNED, rows: OWNED_ROWS })
      expect(order()).toEqual([
        'stats-row-3', // Cy Main's only character: 20k, no heading
        'stats-group-user:Ada', 'stats-row-2', 'stats-row-1', // 13.5k together
        'stats-group-unowned', 'stats-row-4',
      ])
      expect(screen.getByTestId('stats-group-user:Ada')).toHaveTextContent('2 characters')
      expect(screen.getByTestId('stats-group-user:Ada')).toHaveTextContent('13.5k')
      expect(screen.getByTestId('stats-row-3')).toHaveTextContent('Cy Main') // the alt names its main
    })

    it('a user\'s checkbox ticks all their characters; each character can still be ticked alone', async () => {
      const onToggleMany = vi.fn()
      const { onToggle } = setup({ pilots: OWNED, rows: OWNED_ROWS, onToggleMany })
      await userEvent.click(screen.getByRole('checkbox', { name: 'Isolate all of Ada\'s characters' }))
      expect(onToggleMany).toHaveBeenCalledWith([2, 1], true)
      await userEvent.click(screen.getByRole('checkbox', { name: 'Isolate Cy' }))
      expect(onToggle).toHaveBeenCalledWith(3)
    })

    it('a fully ticked user unticks as one; a partly ticked one shows as mixed', async () => {
      const onToggleMany = vi.fn()
      const view = render(
        <StatsTable
          brId="br1" rows={OWNED_ROWS} totals={TOTALS} pilots={OWNED} family="damage" direction="out"
          selected={new Set([1])} onToggle={vi.fn()} onToggleMany={onToggleMany}
          range={{ from: 1000, to: 1100 }} scope="all"
        />,
      )
      const box = () => screen.getByRole('checkbox', { name: 'Isolate all of Ada\'s characters' }) as HTMLInputElement
      expect(box().indeterminate).toBe(true)
      view.rerender(
        <StatsTable
          brId="br1" rows={OWNED_ROWS} totals={TOTALS} pilots={OWNED} family="damage" direction="out"
          selected={new Set([1, 2])} onToggle={vi.fn()} onToggleMany={onToggleMany}
          range={{ from: 1000, to: 1100 }} scope="all"
        />,
      )
      expect(box().checked).toBe(true)
      await userEvent.click(box())
      expect(onToggleMany).toHaveBeenCalledWith([2, 1], false)
    })

    it('keeps the viewer\'s own characters in their own section above the users', () => {
      setup({ pilots: [{ ...OWNED[0], is_self: true }, ...OWNED.slice(1)], rows: OWNED_ROWS })
      expect(order().slice(0, 3)).toEqual(['stats-group-own', 'stats-row-1', 'stats-group-others'])
    })
  })

  it('repeats the ticked pilots side by side at the top once two are ticked', async () => {
    const { onToggle } = setup({ selected: new Set([1, 3]) })
    const block = screen.getAllByTestId(/^stats-compare-/).map((r) => r.getAttribute('data-testid'))
    expect(block).toEqual(['stats-compare-1', 'stats-compare-3']) // sorted like the table
    expect(screen.getByTestId('stats-compare-1')).toHaveTextContent('4.5k')
    expect(screen.getByTestId('stats-row-1')).toBeInTheDocument() // still in its place below
    await userEvent.click(screen.getByRole('checkbox', { name: 'Stop comparing Cy' }))
    expect(onToggle).toHaveBeenCalledWith(3)
  })

  it('has no comparing block for a single ticked pilot', () => {
    setup({ selected: new Set([1]) })
    expect(screen.queryByTestId('stats-group-compare')).not.toBeInTheDocument()
  })

  it('says so when nothing was logged in the range', () => {
    setup({ rows: [row(1, {}), row(2, {})] })
    expect(screen.getByTestId('stats-nothing')).toBeInTheDocument()
  })

  it('expanding a row loads that pilot\'s breakdown for the range', async () => {
    const resp: ContributionsResponse = {
      from_ts: 1000, to_ts: 1100, scope: 'character',
      rows: [
        { source_character_id: 1, source_name: 'Ada', target_name: 'Bad Guy', target_ship: 'Legion',
          effect_type: 'damage', direction: 'out', group: 'damage', value: 4500,
          module_name: '720mm Howitzer', icon_type_id: 1, weapon_category: null, quality: 'Hits',
          hits: 12, min_hit: 40, max_hit: 1500, quality_counts: { Hits: 9, Wrecks: 3, Misses: 3 } },
        { source_character_id: 1, source_name: 'Ada', target_name: 'Other Guy', target_ship: null,
          effect_type: 'damage', direction: 'in', group: 'damage', value: 700,
          module_name: null, icon_type_id: null, weapon_category: null, quality: null },
      ],
    }
    vi.mocked(api.characterSnapshot).mockResolvedValue(resp)
    setup()
    await userEvent.click(screen.getByRole('button', { name: 'Show breakdown for Ada' }))
    const table = await screen.findByTestId('breakdown-1')
    await waitFor(() => expect(api.characterSnapshot).toHaveBeenCalledWith('br1', '1', 1000, 1100))
    expect(table).toHaveTextContent('Bad Guy')
    expect(table).toHaveTextContent('720mm Howitzer')
    expect(table).toHaveTextContent('Wrecks 3')
    expect(table).toHaveTextContent('Misses 3')
    expect(table).not.toHaveTextContent('Other Guy') // incoming row, table is outgoing
  })
})

describe('breakdownRows', () => {
  const rep = (source: string, target: string) => ({
    source_character_id: null, source_name: source, target_name: target, target_ship: 'Loki',
    effect_type: 'rep_armor', direction: 'out', group: 'damage', value: 100,
    module_name: null, icon_type_id: null, weapon_category: null, quality: null,
  })

  it('reads reps as outgoing when the pilot applied them and incoming when they received them', () => {
    const rows = [rep('Ada', 'Bo'), rep('Cy', 'Ada')]
    expect(breakdownRows(rows, 'Ada', 'reps', 'out').map((r) => r.other)).toEqual(['Bo'])
    expect(breakdownRows(rows, 'Ada', 'reps', 'in').map((r) => r.other)).toEqual(['Cy'])
  })

  it('keeps only the stat type asked for', () => {
    expect(breakdownRows([rep('Ada', 'Bo')], 'Ada', 'damage', 'out')).toEqual([])
  })
})
