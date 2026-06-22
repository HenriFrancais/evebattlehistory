// zKillboard-style battle-report timeline: a column table grouped into
// year-month sections, newest first. Each row marks the entities involved
// (us vs the largest opponent), the result, ISK in/out, pilot counts, whether
// the viewer was present, and log-upload coverage (the viewer's own characters
// and the whole NV roster present).

import { Fragment } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { BrSummary } from '../api'
import { fmtIsk, fmtDateTime } from '../format'

function ResultBadge({ result }: { result: string | null }) {
  if (!result) return <span className="badge badge-pending">Pending</span>
  const cls = result === 'win' ? 'badge-win' : result === 'loss' ? 'badge-loss' : 'badge-tie'
  return <span className={`badge ${cls}`} data-testid="result-badge">{result}</span>
}

/** "covered / present" with a colour cue when fully covered (and any present). */
function Coverage({ logged, present, label }: { logged: number; present: number; label: string }) {
  if (present === 0) return <span className="dim">{label} —</span>
  const full = logged >= present
  return (
    <span>
      <span className="dim">{label} </span>
      <span className={full ? 'cov-covered' : 'cov-missing'}>{logged}/{present}</span>
    </span>
  )
}

function monthKey(br: BrSummary): string {
  return (br.battle_at ?? br.created_at).slice(0, 7) // YYYY-MM (UTC)
}

/** System names, each a zKillboard link when its system id is known. */
function SystemLinks({ br }: { br: BrSummary }) {
  const names = br.systems ?? []
  if (names.length === 0) return <>—</>
  const ids = br.system_ids ?? []
  return (
    <>
      {names.map((name, i) => (
        <Fragment key={i}>
          {i > 0 && ', '}
          {ids[i] != null ? (
            <a
              href={`https://zkillboard.com/system/${ids[i]}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="tl-sys-link"
              onClick={(e) => e.stopPropagation()}
            >
              {name}
            </a>
          ) : (
            name
          )}
        </Fragment>
      ))}
    </>
  )
}

export function BrTimelineTable({ brs }: { brs: BrSummary[] }) {
  const navigate = useNavigate()
  // Newest battle first.
  const sorted = [...brs].sort((a, b) => {
    const ta = a.battle_at ?? a.created_at
    const tb = b.battle_at ?? b.created_at
    return tb.localeCompare(ta)
  })

  // Group into year-month buckets, preserving the newest-first order.
  const groups: { key: string; rows: BrSummary[] }[] = []
  for (const br of sorted) {
    const key = monthKey(br)
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.rows.push(br)
    else groups.push({ key, rows: [br] })
  }

  if (sorted.length === 0) return <p className="dim">No battle reports yet.</p>

  // One table for the whole timeline so columns stay globally aligned across
  // every year-month; each month is a <tbody> introduced by a full-width
  // header row.
  return (
    <div data-testid="timeline-table">
      <table className="tl-table">
        <thead>
          <tr>
            <th>Battle</th>
            <th>System</th>
            <th>Entities</th>
            <th>Result</th>
            <th className="tl-num">ISK killed</th>
            <th className="tl-num">ISK lost</th>
            <th className="tl-num">Pilots</th>
            <th className="tl-center">Present</th>
            <th>Logs</th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g.key} className="tl-month">
            <tr className="tl-month-row">
              <th colSpan={9} scope="colgroup">
                <h2 className="tl-month-head">{g.key}</h2>
              </th>
            </tr>
            {g.rows.map((br) => (
              <tr
                key={br.br_id}
                data-testid="timeline-row"
                className="tl-row-link"
                onClick={(e) => {
                  // Let inner links/buttons (system zkill link, title) handle their own clicks.
                  if ((e.target as HTMLElement).closest('a, button')) return
                  navigate(`/brs/${br.br_id}`)
                }}
              >
                <td>
                  <Link to={`/brs/${br.br_id}`} className="tl-title">
                    {br.title ?? `BR ${br.br_id}`}
                  </Link>
                  <div className="dim tl-sub">
                    {fmtDateTime(br.battle_at ?? br.created_at)}
                  </div>
                </td>
                <td><SystemLinks br={br} /></td>
                <td>
                  <span className="side-us">{br.our_name ?? 'Us'}</span>
                  <span className="dim"> vs </span>
                  <span className="side-them">{br.opponent_name ?? '—'}</span>
                </td>
                <td><ResultBadge result={br.result} /></td>
                <td className="tl-num">{fmtIsk(br.our_isk_destroyed)}</td>
                <td className="tl-num">{fmtIsk(br.our_isk_lost)}</td>
                <td className="tl-num">
                  <span className="side-us">{br.friendly_pilots ?? 0}</span>
                  <span className="dim"> v </span>
                  <span className="side-them">{br.enemy_pilots ?? 0}</span>
                </td>
                <td className="tl-center">
                  {br.you_present
                    ? <span className="cov-covered" title="You were present">✓</span>
                    : <span className="dim" title="You were not present">✗</span>}
                </td>
                <td className="tl-logs">
                  <Coverage logged={br.your_logged ?? 0} present={br.your_present ?? 0} label="you" />
                  <Coverage logged={br.roster_logged ?? 0} present={br.roster_present ?? 0} label="all" />
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  )
}
