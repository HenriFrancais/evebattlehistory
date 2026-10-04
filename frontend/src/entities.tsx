// Entity directory: who is in which corporation / alliance for one battle report.
//
// Loaded once per BR by the page shell and shared through context, so every place
// that names a pilot can show `Name [CORP] <ALLI>` without its own request.
import { createContext, useContext } from 'react'
import type { BrEntities } from './api'

export interface PilotTags {
  corpTicker: string | null
  allianceTicker: string | null
  corpName: string | null
  allianceName: string | null
  corporationId: number | null
  allianceId: number | null
}

export interface EntityIndex {
  byId(id: number | null | undefined): PilotTags | null
  byName(name: string | null | undefined): PilotTags | null
}

const EMPTY: EntityIndex = { byId: () => null, byName: () => null }

export function buildEntityIndex(e: BrEntities | null): EntityIndex {
  if (!e) return EMPTY
  const corps = new Map(e.corporations.map((c) => [c.corporation_id, c]))
  const allis = new Map(e.alliances.map((a) => [a.alliance_id, a]))
  const ids = new Map<number, PilotTags>()
  const names = new Map<string, PilotTags>()
  for (const c of e.characters) {
    const corp = c.corporation_id != null ? corps.get(c.corporation_id) : undefined
    const allianceId = c.alliance_id ?? corp?.alliance_id ?? null
    const alli = allianceId != null ? allis.get(allianceId) : undefined
    const tags: PilotTags = {
      corpTicker: corp?.ticker ?? null,
      allianceTicker: alli?.ticker ?? null,
      corpName: corp?.name ?? null,
      allianceName: alli?.name ?? null,
      corporationId: c.corporation_id,
      allianceId,
    }
    ids.set(c.character_id, tags)
    names.set(c.name.toLowerCase(), tags)
  }
  // Names on no killmail: only the tickers the log parser saw beside them.
  for (const n of e.by_name) {
    const key = n.name.toLowerCase()
    if (names.has(key)) continue
    names.set(key, {
      corpTicker: n.corp_ticker,
      allianceTicker: n.alliance_ticker,
      corpName: null,
      allianceName: null,
      corporationId: null,
      allianceId: null,
    })
  }
  return {
    byId: (id) => (id == null ? null : ids.get(id) ?? null),
    byName: (name) => (name ? names.get(name.toLowerCase()) ?? null : null),
  }
}

export const EntityContext = createContext<EntityIndex>(EMPTY)

export function useEntities(): EntityIndex {
  return useContext(EntityContext)
}

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Plain-text tickers: ` [CORP] <ALLI>`, or '' when neither is known. */
export function tickerText(tags: PilotTags | null): string {
  if (!tags) return ''
  return (
    (tags.corpTicker ? ` [${tags.corpTicker}]` : '') +
    (tags.allianceTicker ? ` <${tags.allianceTicker}>` : '')
  )
}

/** Full corporation / alliance names for a tooltip, or undefined when unknown. */
export function affiliationTitle(tags: PilotTags | null): string | undefined {
  if (!tags) return undefined
  const parts = [tags.corpName, tags.allianceName].filter((p): p is string => !!p)
  return parts.length ? parts.join(' · ') : undefined
}

/**
 * Escaped HTML for surfaces that build markup as a string (chart tooltips). The
 * result is assigned to innerHTML, so the name and both tickers are escaped here.
 */
export function pilotLabel(name: string, tags: PilotTags | null): string {
  const t = tickerText(tags)
  return esc(name) + (t ? `<span class="pilot-tags">${esc(t)}</span>` : '')
}
