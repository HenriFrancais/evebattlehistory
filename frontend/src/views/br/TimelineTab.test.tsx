import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FleetTimeline, PilotTimeline } from '../../api'
import { resetCache } from '../../cache'
import { TimelineTab } from './TimelineTab'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      fleetTimeline: vi.fn(), pilotTimeline: vi.fn(), broadcasts: vi.fn(), characterSnapshot: vi.fn(),
    },
  }
})
// uPlot needs a canvas; the chart itself is not what these tests are about.
vi.mock('uplot', () => ({
  default: vi.fn().mockImplementation(() => ({ destroy: vi.fn(), setSize: vi.fn() })),
}))
import { api } from '../../api'

const X = [1000, 1005, 1010]
const FLEET: FleetTimeline = {
  x: X, bucket_seconds: 5, t_start: 1000, t_end: 1010, kills: [], leaders: [],
  fights: [{ fight_id: 1, seq: 0, system_id: 1, started_at: '1970-01-01T00:16:40', ended_at: '1970-01-01T00:16:55' }],
  series: [
    { key: 'damage:out', effect_type: 'damage', direction: 'out', metric: 'amount', values: [100, 300, 50] },
    { key: 'rep_armor:out', effect_type: 'rep_armor', direction: 'out', metric: 'amount', values: [null, 800, null] },
  ],
}
const PILOTS: PilotTimeline = {
  x: X, bucket_seconds: 5, scope: 'all',
  pilots: [
    { character_id: 1, character_name: 'Ada', ship_type_id: null, ship_name: 'Loki', side_kind: 'friendly', is_self: false,
      series: [
        { effect_type: 'damage', direction: 'out', idx: [0, 1], sum: [100, 300], count: [2, 3], min: [40, 50], max: [60, 150] },
        { effect_type: 'damage', direction: 'in', idx: [1], sum: [900], count: [9], min: [10], max: [400] },
      ] },
    { character_id: 2, character_name: 'Bo', ship_type_id: null, ship_name: 'Guardian', side_kind: 'friendly', is_self: false,
      series: [
        { effect_type: 'rep_armor', direction: 'out', idx: [1], sum: [800], count: [2], min: [400], max: [400] },
        { effect_type: 'damage', direction: 'out', idx: [2], sum: [50], count: [1], min: [50], max: [50] },
      ] },
  ],
}

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.search}</output>
}
function setup(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/brs/br1/timeline${search}`]}>
      <TimelineTab brId="br1" />
      <Where />
    </MemoryRouter>,
  )
}
const pressed = (group: string) =>
  within(screen.getByRole('group', { name: group })).getAllByRole('button', { pressed: true })
    .map((b) => b.textContent)

describe('TimelineTab', () => {
  beforeEach(() => {
    resetCache()
    vi.mocked(api.fleetTimeline).mockReset().mockResolvedValue(FLEET)
    vi.mocked(api.pilotTimeline).mockReset().mockResolvedValue(PILOTS)
    vi.mocked(api.broadcasts).mockReset().mockResolvedValue([])
  })

  it('opens on damage, both directions, with the whole fight in the table', async () => {
    setup()
    await screen.findByTestId('stats-row-1')
    expect(pressed('Stat type')).toEqual(['Damage'])
    expect(pressed('Direction')).toEqual(['Both'])
    expect(screen.getByTestId('tl-selection')).toHaveTextContent('whole fight')
    expect(screen.getByTestId('table-direction-note')).toBeInTheDocument()
    expect(screen.getByTestId('stats-row-1')).toHaveTextContent('400')
  })

  it('reads stat type, direction and isolated pilots from the URL', async () => {
    setup('?stat=reps&dir=out&pilots=2')
    await screen.findByTestId('stats-row-2')
    expect(pressed('Stat type')).toEqual(['Reps'])
    expect(pressed('Direction')).toEqual(['Outgoing'])
    expect(screen.getByTestId('isolation-bar')).toHaveTextContent('1 pilot isolated')
    expect(screen.getByRole('button', { name: /^Cycles/ })).toBeInTheDocument()
  })

  it('switching stat type and direction updates the table and the URL', async () => {
    setup()
    await screen.findByTestId('stats-row-1')
    await userEvent.click(screen.getByRole('button', { name: 'Incoming' }))
    expect(screen.getByTestId('stats-row-1')).toHaveTextContent('900')
    expect(screen.queryByTestId('stats-row-2')).not.toBeInTheDocument() // Bo took nothing
    await userEvent.click(screen.getByRole('button', { name: 'Reps' }))
    expect(screen.getByTestId('where')).toHaveTextContent('stat=reps')
    expect(screen.getByTestId('where')).toHaveTextContent('dir=in')
  })

  it('ticking a pilot isolates them; clearing shows the whole fleet again', async () => {
    setup()
    await screen.findByTestId('stats-row-1')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Isolate Ada' }))
    expect(screen.getByTestId('isolation-bar')).toHaveTextContent('1 pilot isolated')
    expect(screen.getByTestId('where')).toHaveTextContent('pilots=1')
    await userEvent.click(screen.getByTestId('isolation-clear'))
    expect(screen.queryByTestId('isolation-bar')).not.toBeInTheDocument()
    expect(screen.getByTestId('where')).not.toHaveTextContent('pilots')
  })

  it('drops a pilot id that is not in the data', async () => {
    setup('?pilots=1,999')
    await screen.findByTestId('stats-row-1')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/pilots=1$/))
    expect(screen.getByTestId('isolation-bar')).toHaveTextContent('1 pilot isolated')
  })

  it('switching a series chip off removes it from the table', async () => {
    setup('?dir=out')
    await screen.findByTestId('stats-row-1')
    await userEvent.click(screen.getByRole('button', { name: 'Damage applied' }))
    expect(screen.getByTestId('stats-nothing')).toBeInTheDocument()
  })

  it('typing a UTC start time zooms the chart and narrows the table', async () => {
    setup('?dir=out')
    await screen.findByTestId('stats-row-1')
    const from = screen.getByTestId('range-from-input')
    expect(from).toHaveValue('00:16:25') // fight start 00:16:40 less the 15 s buffer
    await userEvent.clear(from)
    await userEvent.type(from, '00:16:45{Enter}')
    expect(screen.getByTestId('range-label')).toHaveTextContent('00:16:45 →')
    expect(screen.getByTestId('tl-selection')).toHaveTextContent('zoomed in')
    expect(screen.getByTestId('stats-row-1')).toHaveTextContent('300') // bucket 1005 only
    await userEvent.click(screen.getByTestId('range-clear'))
    expect(screen.getByTestId('tl-selection')).toHaveTextContent('whole fight')
  })

  it('rejects a time outside the fight or not a time at all', async () => {
    setup()
    await screen.findByTestId('stats-row-1')
    const to = screen.getByTestId('range-to-input')
    for (const bad of ['banana', '25:00:00', '09:00:00']) {
      await userEvent.clear(to)
      await userEvent.type(to, `${bad}{Enter}`)
      expect(to).toHaveAttribute('aria-invalid', 'true')
      expect(screen.getByTestId('tl-selection')).toHaveTextContent('whole fight')
    }
  })

  it('keeps the chart when per-pilot statistics fail to load', async () => {
    vi.mocked(api.pilotTimeline).mockRejectedValue(new Error('boom'))
    setup()
    expect(await screen.findByTestId('stats-error')).toHaveTextContent('boom')
    expect(screen.getByTestId('timeline-chart-area')).toBeInTheDocument()
  })

  it('prompts for logs when the battle has none', async () => {
    vi.mocked(api.fleetTimeline).mockResolvedValue({ ...FLEET, x: [], series: [] })
    setup()
    const empty = await screen.findByTestId('timeline-empty')
    expect(within(empty).getByRole('link', { name: 'Upload logs' })).toHaveAttribute('href', '/logs')
  })

  it('shows the error when the timeline itself fails to load', async () => {
    vi.mocked(api.fleetTimeline).mockRejectedValue(new Error('nope'))
    setup()
    expect(await screen.findByTestId('timeline-error')).toHaveTextContent('nope')
  })
})
