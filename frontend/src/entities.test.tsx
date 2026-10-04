import { render, screen } from '@testing-library/react'
import type { BrEntities } from './api'
import { PilotName } from './components/PilotName'
import { EntityContext, buildEntityIndex, pilotLabel, tickerText } from './entities'

const ENTITIES: BrEntities = {
  characters: [
    { character_id: 1, name: 'Ada Alpha', corporation_id: 10, alliance_id: 20 },
    { character_id: 2, name: 'Bo Bravo', corporation_id: 11, alliance_id: null },
    { character_id: 3, name: 'Cy Charlie', corporation_id: null, alliance_id: null },
  ],
  corporations: [
    { corporation_id: 10, name: 'Ten Corp', ticker: 'TEN', alliance_id: 20 },
    { corporation_id: 11, name: 'Eleven Corp', ticker: 'ELVN', alliance_id: null },
  ],
  alliances: [{ alliance_id: 20, name: 'Twenty Alliance', ticker: 'TWNTY' }],
  by_name: [
    { name: 'Dee Delta', corp_ticker: 'EVIL', alliance_ticker: 'BAD' },
    { name: 'Ada Alpha', corp_ticker: 'WRONG', alliance_ticker: null },
  ],
}
const index = buildEntityIndex(ENTITIES)

describe('entity index', () => {
  it('finds tags by character id', () => {
    expect(index.byId(1)).toMatchObject({
      corpTicker: 'TEN', allianceTicker: 'TWNTY', corpName: 'Ten Corp', allianceName: 'Twenty Alliance',
    })
  })

  it('finds tags by name, ignoring case', () => {
    expect(tickerText(index.byName('ada alpha'))).toBe(' [TEN] <TWNTY>')
  })

  it('omits a ticker that is not known', () => {
    expect(tickerText(index.byId(2))).toBe(' [ELVN]')
    expect(tickerText(index.byId(3))).toBe('')
  })

  it('falls back to log-seen tickers for a name with no character', () => {
    expect(tickerText(index.byName('Dee Delta'))).toBe(' [EVIL] <BAD>')
  })

  it('prefers the character over a log-seen entry of the same name', () => {
    expect(index.byName('Ada Alpha')?.corpTicker).toBe('TEN')
  })

  it('returns null for unknown ids and names, and for no directory at all', () => {
    expect(index.byId(99)).toBeNull()
    expect(index.byName('Nobody')).toBeNull()
    expect(buildEntityIndex(null).byId(1)).toBeNull()
  })
})

describe('pilotLabel', () => {
  it('escapes name and tickers', () => {
    const html = pilotLabel('<img src=x onerror=alert(1)>', {
      corpTicker: 'A<B', allianceTicker: '"Q"', corpName: null, allianceName: null,
      corporationId: null, allianceId: null,
    })
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).toContain('[A&lt;B] &lt;&quot;Q&quot;&gt;')
  })

  it('is just the escaped name when no tickers are known', () => {
    expect(pilotLabel('A & B', null)).toBe('A &amp; B')
  })
})

describe('PilotName', () => {
  it('shows the name with tickers and the full names as a tooltip', () => {
    render(
      <EntityContext.Provider value={index}>
        <PilotName name="Ada Alpha" characterId={1} />
      </EntityContext.Provider>,
    )
    const el = screen.getByText('Ada Alpha', { exact: false })
    expect(el).toHaveTextContent('Ada Alpha [TEN] <TWNTY>')
    expect(el).toHaveAttribute('title', 'Ten Corp · Twenty Alliance')
  })

  it('shows just the name outside any directory', () => {
    render(<PilotName name="Solo Pilot" />)
    expect(screen.getByText('Solo Pilot')).toHaveTextContent(/^Solo Pilot$/)
  })
})
