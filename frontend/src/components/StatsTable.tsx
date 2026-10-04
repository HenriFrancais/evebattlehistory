// Per-pilot statistics for the range selected on the timeline chart. One row per
// pilot the viewer may see; ticking rows isolates those pilots on the chart;
// expanding a row shows who they hit (or were hit by) and with what.
//
// The viewer's own characters come first, in a section of their own. For FC / High
// Command the other pilots are grouped by the user who owns them (`owner`), with a
// checkbox per user as well as per character. While two or more pilots are ticked,
// they are also repeated side by side in a "Comparing" block at the very top; their
// rows stay where they are below, so the table does not jump while ticking.
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
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

/** Rows shown together under one heading. */
interface Group {
  key: string
  /** Heading text; null = no heading row (a user's only character, or a flat table). */
  label: string | null
  /** The heading carries a checkbox that ticks every row in the group. */
  pickable: boolean
  rows: PilotStatRow[]
}

/** A group's value in the sorted column: sums where they add up, else the best row. */
function groupValue(rows: PilotStatRow[], key: SortKey): number | null {
  const vals = rows.map((r) => cell(r, key)).filter((v): v is number => v != null)
  if (vals.length === 0) return null
  if (key === 'min') return Math.min(...vals)
  if (key === 'peak' || key === 'max') return Math.max(...vals)
  return vals.reduce((a, b) => a + b, 0)
}

/** Checkbox for a whole group: ticked when every row is, mixed when only some are. */
function GroupCheckbox({ ids, selected, label, onChange }: {
  ids: number[]
  selected: Set<number>
  label: string
  onChange: (on: boolean) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const count = ids.filter((id) => selected.has(id)).length
  const all = count === ids.length
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = count > 0 && !all
  }, [count, all])
  return (
    <input ref={ref} type="checkbox" checked={all} aria-label={label} onChange={() => onChange(!all)} />
  )
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
  /** Tick or untick several pilots at once (a group's checkbox). */
  onToggleMany?: (characterIds: number[], on: boolean) => void
  /** Chart colour of each pilot being compared; none when not comparing. */
  colors?: Map<number, { color: string; dash?: number[] }>
  range: { from: number; to: number }
  /** 'own' = the viewer may isolate, and open the breakdown of, their own characters only. */
  scope: 'all' | 'own'
}

export function StatsTable({
  brId, rows, totals, pilots, family, direction, selected, onToggle, onToggleMany, colors,
  range, scope,
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
  const active = useMemo(() => sorted.filter((r) => !isIdle(r) || selected.has(r.characterId)), [sorted, selected])
  const idleCount = sorted.length - active.length
  const visible = showIdle ? sorted : active

  // The ticked pilots, repeated together at the top, in the table's sort order.
  const compared = useMemo(
    () => (selected.size >= 2 ? sorted.filter((r) => selected.has(r.characterId)) : []),
    [sorted, selected],
  )

  // Sections, top to bottom: the viewer's own characters, then everyone else — by
  // owning user when the viewer is told who owns whom (FC / High Command).
  const groups = useMemo(() => {
    const own = visible.filter((r) => byId.get(r.characterId)?.is_self)
    const others = visible.filter((r) => !byId.get(r.characterId)?.is_self)
    const out: Group[] = []
    if (own.length > 0) {
      out.push({ key: 'own', label: 'Your characters', pickable: own.length > 1, rows: own })
    }
    const grouped = others.some((r) => byId.get(r.characterId)?.owner)
    if (!grouped) {
      if (others.length > 0) {
        out.push({ key: 'others', label: own.length > 0 ? 'Other pilots' : null, pickable: false, rows: others })
      }
      return out
    }
    const byOwner = new Map<string, PilotStatRow[]>()
    const unowned: PilotStatRow[] = []
    for (const r of others) {
      const owner = byId.get(r.characterId)?.owner
      if (!owner) unowned.push(r)
      else byOwner.set(owner, [...(byOwner.get(owner) ?? []), r])
    }
    const users: Group[] = [...byOwner.entries()].map(([owner, rs]) => ({
      key: `user:${owner}`, label: rs.length > 1 ? owner : null, pickable: true, rows: rs,
    }))
    const dir = sort.desc ? -1 : 1
    const text = (g: Group) => (
      sortKey === 'ship' ? byId.get(g.rows[0].characterId)?.ship_name ?? '' : g.key.slice(5)
    ).toLowerCase()
    users.sort((a, b) => {
      if (sortKey === 'name' || sortKey === 'ship') return text(a).localeCompare(text(b)) * dir
      const va = groupValue(a.rows, sortKey)
      const vb = groupValue(b.rows, sortKey)
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      return (va - vb) * dir
    })
    // A heading between the viewer's own section and the first user, so a user's
    // lone character (no heading of its own) does not read as one of the viewer's.
    if (own.length > 0) out.push({ key: 'others', label: 'Other pilots, by user', pickable: false, rows: [] })
    out.push(...users)
    if (unowned.length > 0) {
      out.push({ key: 'unowned', label: 'Not in roster', pickable: false, rows: unowned })
    }
    return out
  }, [visible, byId, sort.desc, sortKey])

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
          You can isolate your own characters on the chart and open their target and
          weapon breakdown. FC and High Command can do so for every pilot.
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
            {compared.length > 0 && (
              <>
                <tr className="stats-group" data-testid="stats-group-compare">
                  <td className="stats-pick" />
                  <td colSpan={columns.length}>
                    Comparing
                    <span className="dim stats-group-count"> · {compared.length} pilots</span>
                  </td>
                </tr>
                {compared.map((r) => {
                  const p = byId.get(r.characterId)
                  if (!p) return null
                  const swatch = colors?.get(r.characterId)
                  return (
                    <tr key={r.characterId} className="stats-row stats-compare" data-testid={`stats-compare-${r.characterId}`}>
                      <td className="stats-pick">
                        <input
                          type="checkbox" checked
                          onChange={() => onToggle(r.characterId)}
                          aria-label={`Stop comparing ${p.character_name}`}
                        />
                        {swatch && (
                          <span
                            aria-hidden
                            className={swatch.dash ? 'compare-swatch dashed' : 'compare-swatch'}
                            style={{ color: swatch.color }}
                          />
                        )}
                      </td>
                      <td><PilotName name={p.character_name} characterId={p.character_id} /></td>
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
                  )
                })}
              </>
            )}
            {groups.map((g) => (
              <Fragment key={g.key}>
                {g.label != null && (
                  <tr className="stats-group" data-testid={`stats-group-${g.key}`}>
                    <td className="stats-pick">
                      {g.pickable && onToggleMany && (
                        <GroupCheckbox
                          ids={g.rows.map((r) => r.characterId)}
                          selected={selected}
                          label={g.key === 'own' ? 'Isolate all your characters' : `Isolate all of ${g.label}'s characters`}
                          onChange={(on) => onToggleMany(g.rows.map((r) => r.characterId), on)}
                        />
                      )}
                    </td>
                    <td colSpan={2}>
                      {g.label}
                      {g.key.startsWith('user:') && (
                        <span className="dim stats-group-count"> · {g.rows.length} characters</span>
                      )}
                    </td>
                    {columns.slice(2).map((c) => (
                      <td key={c.key} className="num">
                        {c.key === 'total' && g.pickable && g.rows.length > 1 ? fmtCell('total', groupValue(g.rows, 'total')) : null}
                      </td>
                    ))}
                  </tr>
                )}
                {g.rows.map((r) => {
              const p = byId.get(r.characterId)
              if (!p) return null
              const owner = g.label == null && p.owner && p.owner !== p.character_name ? p.owner : null
              const swatch = colors?.get(r.characterId)
              // Isolating a pilot and opening their breakdown (who they shot, with
              // what) are FC/HC-only except for the viewer's own characters. The
              // server enforces the breakdown rule.
              const canExpand = scope === 'all' || p.is_self
              const isOpen = canExpand && expanded.has(r.characterId)
              const picked = selected.has(r.characterId)
              return (
                <Fragment key={r.characterId}>
                  <tr
                    className={`stats-row${picked ? ' picked' : ''}${g.key.startsWith('user:') && g.label != null ? ' in-group' : ''}`}
                    data-testid={`stats-row-${r.characterId}`}>
                    <td className="stats-pick">
                      {canExpand && (
                        <input
                          type="checkbox"
                          checked={picked}
                          onChange={() => onToggle(r.characterId)}
                          aria-label={`Isolate ${p.character_name}`}
                        />
                      )}
                      {swatch && (
                        <span
                          aria-hidden
                          className={swatch.dash ? 'compare-swatch dashed' : 'compare-swatch'}
                          style={{ color: swatch.color }}
                        />
                      )}
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
                      {owner && <span className="dim stats-owner" title="This character's main"> · {owner}</span>}
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
              </Fragment>
            ))}
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
