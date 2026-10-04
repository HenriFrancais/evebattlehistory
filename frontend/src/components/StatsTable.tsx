// Per-pilot statistics for the range selected on the timeline chart. One row per
// pilot the viewer may see; ticking rows isolates those pilots on the chart;
// expanding a row shows who they hit (or were hit by) and with what.
import { Fragment, useMemo, useState } from 'react'
import type { PilotTimelineRow } from '../api'
import { fmtCompact } from '../format'
import type { PilotStatRow, StatFamily } from '../pilotStats'
import { isIdle } from '../pilotStats'
import { Caret, SortArrow } from './Icons'
import { PilotBreakdown } from './PilotBreakdown'
import { PilotName } from './PilotName'

type SortKey = 'name' | 'ship' | 'total' | 'peak' | 'avgPerSec' | 'min' | 'max' | 'hits' | 'misses'

interface Column {
  key: SortKey
  label: string
  title: string
  numeric: boolean
}

function columnsFor(family: StatFamily): Column[] {
  const unit = family === 'cap' ? 'GJ' : family === 'ewar' ? 'applications' : 'HP'
  const one = family === 'damage' ? 'hit' : 'cycle'
  const cols: Column[] = [
    { key: 'name', label: 'Pilot', title: 'Pilot, corporation and alliance', numeric: false },
    { key: 'ship', label: 'Ship', title: 'Hull flown', numeric: false },
    { key: 'total', label: 'Total', title: `Total ${unit} in the selected range`, numeric: true },
  ]
  if (family === 'ewar') {
    cols.push({ key: 'peak', label: 'Peak', title: 'Most applications in one 5-second bucket', numeric: true })
    return cols
  }
  cols.push(
    { key: 'peak', label: 'Peak /s', title: 'Busiest 5-second bucket, per second', numeric: true },
    { key: 'avgPerSec', label: 'Avg /s', title: 'Total divided by the length of the range', numeric: true },
    { key: 'min', label: 'Min', title: `Smallest single ${one}`, numeric: true },
    { key: 'max', label: 'Max', title: `Largest single ${one}`, numeric: true },
    { key: 'hits', label: family === 'damage' ? 'Hits' : 'Cycles', title: `Number of ${one}s`, numeric: true },
  )
  if (family === 'damage') {
    cols.push({ key: 'misses', label: 'Misses', title: 'Shots that missed completely', numeric: true })
  }
  return cols
}

function cell(row: PilotStatRow, key: SortKey): number | null {
  switch (key) {
    case 'total': return row.total
    case 'peak': return row.peak
    case 'avgPerSec': return row.avgPerSec
    case 'min': return row.min
    case 'max': return row.max
    case 'hits': return row.hits
    case 'misses': return row.misses
    default: return null
  }
}

function fmtCell(key: SortKey, v: number | null) {
  if (v == null) return <span className="stats-blank" aria-label="not recorded">–</span>
  if (key === 'hits' || key === 'misses') return v.toLocaleString('en-US')
  return fmtCompact(v)
}

interface Props {
  brId: string
  rows: PilotStatRow[]
  totals: PilotStatRow
  pilots: PilotTimelineRow[]
  family: StatFamily
  /** Direction the numbers describe ('both' on the chart shows outgoing here). */
  direction: 'out' | 'in'
  selected: Set<number>
  onToggle: (characterId: number) => void
  range: { from: number; to: number }
  /** 'own' = the viewer may open the breakdown of their own characters only. */
  scope: 'all' | 'own'
}

export function StatsTable({
  brId, rows, totals, pilots, family, direction, selected, onToggle, range, scope,
}: Props) {
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'total', desc: true })
  const [showIdle, setShowIdle] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const columns = useMemo(() => columnsFor(family), [family])
  const byId = useMemo(() => new Map(pilots.map((p) => [p.character_id, p])), [pilots])
  // A column that does not exist for this stat type falls back to Total.
  const sortKey = columns.some((c) => c.key === sort.key) ? sort.key : 'total'

  const sorted = useMemo(() => {
    const dir = sort.desc ? -1 : 1
    const text = (r: PilotStatRow) => {
      const p = byId.get(r.characterId)
      return (sortKey === 'ship' ? p?.ship_name : p?.character_name)?.toLowerCase() ?? ''
    }
    return [...rows].sort((a, b) => {
      if (sortKey === 'name' || sortKey === 'ship') return text(a).localeCompare(text(b)) * dir
      const va = cell(a, sortKey)
      const vb = cell(b, sortKey)
      if (va == null && vb == null) return 0
      if (va == null) return 1 // not-recorded always sorts last
      if (vb == null) return -1
      return (va - vb) * dir
    })
  }, [rows, sort.desc, sortKey, byId])

  // A ticked pilot stays visible even when idle in this range, so it can be unticked.
  const active = sorted.filter((r) => !isIdle(r) || selected.has(r.characterId))
  const idleCount = sorted.length - active.length
  const visible = showIdle ? sorted : active

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key !== 'name' && key !== 'ship' }))
  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className="stats-wrap" data-testid="stats-table">
      {scope === 'own' && rows.length > 0 && (
        <p className="dim stats-note" data-testid="stats-scope-note">
          You can open the target and weapon breakdown for your own characters. FC and
          High Command can open it for every pilot.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="dim stats-note" data-testid="stats-empty">
          No pilot has uploaded a log for this battle yet.
        </p>
      ) : (
        <table className="stats-table">
          <thead>
            <tr>
              <th className="stats-pick"><span className="sr-only">Isolate</span></th>
              {columns.map((c) => {
                const on = sortKey === c.key
                return (
                  <th
                    key={c.key}
                    className={c.numeric ? 'num' : undefined}
                    aria-sort={on ? (sort.desc ? 'descending' : 'ascending') : 'none'}
                  >
                    <button type="button" className="stats-sort" title={c.title} onClick={() => onSort(c.key)}>
                      {c.label}
                      {on && <SortArrow desc={sort.desc} className="stats-sort-arrow" />}
                    </button>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const p = byId.get(r.characterId)
              if (!p) return null
              // The breakdown (who they shot, with what) is FC/HC-only except for
              // the viewer's own characters; the server enforces the same rule.
              const canExpand = scope === 'all' || p.is_self
              const isOpen = canExpand && expanded.has(r.characterId)
              const picked = selected.has(r.characterId)
              return (
                <Fragment key={r.characterId}>
                  <tr className={picked ? 'stats-row picked' : 'stats-row'} data-testid={`stats-row-${r.characterId}`}>
                    <td className="stats-pick">
                      <input
                        type="checkbox"
                        checked={picked}
                        onChange={() => onToggle(r.characterId)}
                        aria-label={`Isolate ${p.character_name}`}
                      />
                    </td>
                    <td>
                      {canExpand ? (
                        <button
                          type="button"
                          className="stats-expand"
                          aria-expanded={isOpen}
                          aria-label={`${isOpen ? 'Hide' : 'Show'} breakdown for ${p.character_name}`}
                          onClick={() => toggleExpanded(r.characterId)}
                        >
                          <Caret open={isOpen} />
                        </button>
                      ) : (
                        <span className="stats-expand stats-expand-none" aria-hidden />
                      )}
                      <PilotName name={p.character_name} characterId={p.character_id} />
                    </td>
                    <td className="stats-ship">
                      {p.ship_type_id != null && (
                        <img
                          className="comp-ship-icon" width={22} height={22} alt=""
                          src={`https://images.evetech.net/types/${p.ship_type_id}/icon?size=32`}
                        />
                      )}
                      {p.ship_name ?? <span className="dim">Unknown</span>}
                    </td>
                    {columns.slice(2).map((c) => (
                      <td key={c.key} className="num">{fmtCell(c.key, cell(r, c.key))}</td>
                    ))}
                  </tr>
                  {isOpen && (
                    <tr className="stats-detail">
                      <td />
                      <td colSpan={columns.length}>
                        <PilotBreakdown
                          brId={brId}
                          characterId={r.characterId}
                          pilotName={p.character_name}
                          from={range.from}
                          to={range.to}
                          family={family}
                          direction={direction}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
            {visible.length === 0 && (
              <tr>
                <td />
                <td colSpan={columns.length} className="dim" data-testid="stats-nothing">
                  Nothing was logged in this range for this stat type and direction.
                </td>
              </tr>
            )}
          </tbody>
          {visible.length > 1 && (
            <tfoot>
              <tr data-testid="stats-totals">
                <td />
                <td>All pilots</td>
                <td />
                {columns.slice(2).map((c) => (
                  <td key={c.key} className="num">{fmtCell(c.key, cell(totals, c.key))}</td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      )}
      {idleCount > 0 && (
        <button type="button" className="btn-mini stats-idle" onClick={() => setShowIdle((v) => !v)}>
          {showIdle ? 'Hide idle pilots' : `Show ${idleCount} idle pilot${idleCount === 1 ? '' : 's'}`}
        </button>
      )}
    </div>
  )
}
