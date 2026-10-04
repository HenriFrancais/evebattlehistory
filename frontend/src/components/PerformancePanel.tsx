// Per-character battle performance. Everyone sees the anonymous fleet distributions
// and their own characters' rows (with their median marked + percentile); FC / High
// Command additionally get the full sortable fleet table.

import { PilotName } from './PilotName'
import { useEffect, useMemo, useState } from 'react'

import { api } from '../api'
import type { BrPerformance, PerfCharRow } from '../api'
import { percentileFaster } from '../broadcasts'

function fmtNum(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v))
}
function fmtS(v: number | null): string {
  return v === null || v === undefined ? '—' : `${v.toFixed(1)}s`
}

const MARKER_PALETTE = ['#42a5f5', '#ffa726', '#66bb6a', '#ec407a', '#ab47bc', '#26c6da']

interface Marker {
  value: number
  name: string
  percentile: number | null
}

/** Histogram of a fleet population over [minSec, maxSec] with the viewer's values
 *  marked. Names live in a legend below so they never overwrite each other. Supports a
 *  negative minSec (diverging axis) with a 0 divider. */
function MarkerHist({
  label,
  unit,
  population,
  markers,
  maxSec,
  binSec,
  minSec = 0,
  betterText = 'faster than',
}: {
  label: string
  unit: string
  population: number[]
  markers: Marker[]
  maxSec: number
  binSec: number
  minSec?: number
  betterText?: string
}) {
  // Legend/markers ordered best -> worst: for switch, response and lead, a smaller
  // value (including negatives) is better.
  const ordered = [...markers].sort((a, b) => a.value - b.value)
  const span = maxSec - minSec
  const nBins = Math.max(1, Math.round(span / binSec))
  const counts = new Array(nBins).fill(0)
  let total = 0
  for (const v of population) {
    total++
    let idx = Math.floor((v - minSec) / binSec)
    idx = Math.max(0, Math.min(nBins - 1, idx)) // clamp outliers into the edge bins
    counts[idx]++
  }
  const max = Math.max(1, ...counts)
  const W = 380
  const H = 120
  const PAD_L = 28
  const PAD_B = 18
  const plotW = W - PAD_L
  const bw = plotW / nBins
  const xOf = (v: number) =>
    PAD_L + Math.max(0, Math.min(plotW, ((v - minSec) / span) * plotW))
  return (
    <div style={{ minWidth: W }}>
      <div className="dim" style={{ fontSize: '0.8rem', marginBottom: '0.25rem' }}>
        {label} <span style={{ opacity: 0.7 }}>(n={total} {unit})</span>
      </div>
      <svg width={W} height={H + PAD_B} role="img" aria-label={label}>
        <text x={PAD_L - 4} y={10} fontSize={10} fill="var(--dim, #8893a7)" textAnchor="end">{max}</text>
        <text x={PAD_L - 4} y={H} fontSize={10} fill="var(--dim, #8893a7)" textAnchor="end">0</text>
        {counts.map((c, i) => (
          <rect
            key={i}
            x={PAD_L + i * bw + 0.5}
            y={H - (c / max) * H}
            width={bw - 1}
            height={(c / max) * H}
            fill="#5b6b8c"
            opacity={0.7}
          >
            <title>{`${(minSec + i * binSec).toFixed(0)}–${(minSec + (i + 1) * binSec).toFixed(0)}s: ${c}`}</title>
          </rect>
        ))}
        <line x1={PAD_L} y1={H} x2={W} y2={H} stroke="var(--border, #888)" strokeWidth={1} />
        {minSec < 0 && maxSec > 0 && (
          <line x1={xOf(0)} y1={0} x2={xOf(0)} y2={H} stroke="var(--border, #888)" strokeWidth={1} strokeDasharray="2 2" />
        )}
        {ordered.map((m, i) => {
          const color = MARKER_PALETTE[i % MARKER_PALETTE.length]
          return (
            <g key={i}>
              <line x1={xOf(m.value)} y1={0} x2={xOf(m.value)} y2={H} stroke={color} strokeWidth={2} />
              <circle cx={xOf(m.value)} cy={0} r={3.5} fill={color} />
            </g>
          )
        })}
        <text x={PAD_L} y={H + 14} fontSize={10} fill="var(--dim, #8893a7)">{minSec}s</text>
        <text x={W} y={H + 14} fontSize={10} fill="var(--dim, #8893a7)" textAnchor="end">{maxSec}s{maxSec > minSec ? '+' : ''}</text>
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem', marginTop: '0.2rem' }}>
        {ordered.map((m, i) => (
          <div key={i} style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: MARKER_PALETTE[i % MARKER_PALETTE.length], display: 'inline-block' }} />
            <span>
              {m.name}: {fmtS(m.value)}
              {m.percentile !== null ? ` — ${betterText} ${m.percentile}% of the fleet` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

type SortKey = keyof Pick<
  PerfCharRow,
  'damage_done' | 'reps_out' | 'kills_on' | 'target_median_switch_s' | 'logi_response_median_s' | 'damage_lead_median_s' | 'deaths_flagged'
>

function PerfTable({
  rows,
  sortable,
}: {
  rows: PerfCharRow[]
  sortable: boolean
}) {
  const [sort, setSort] = useState<SortKey>('damage_done')
  const sorted = useMemo(() => {
    if (!sortable) return rows
    // Response times sort ascending (lower = better); everything else descending.
    const asc = sort === 'target_median_switch_s' || sort === 'logi_response_median_s'
    // Nulls (no data) always sort to the bottom, regardless of direction.
    const nullVal = asc ? Infinity : -Infinity
    const val = (r: PerfCharRow) => {
      const v = r[sort]
      return v === null || v === undefined ? nullVal : (v as number)
    }
    return [...rows].sort((a, b) => (asc ? val(a) - val(b) : val(b) - val(a)))
  }, [rows, sort, sortable])

  const Th = ({ k, children, num = true }: { k?: SortKey; children: React.ReactNode; num?: boolean }) => (
    <th
      style={{ textAlign: num ? 'center' : 'left', cursor: sortable && k ? 'pointer' : 'default' }}
      onClick={sortable && k ? () => setSort(k) : undefined}
      data-active={sortable && k === sort}
    >
      {children}
      {sortable && k === sort ? ' ▾' : ''}
    </th>
  )

  return (
    <table className="data-table" style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>
      <thead>
        <tr>
          <Th num={false}>Main</Th>
          <Th num={false}>Character</Th>
          <Th k="damage_done">Damage</Th>
          <Th k="reps_out">Reps</Th>
          <Th k="kills_on">Kills</Th>
          <Th k="target_median_switch_s">Switch</Th>
          <Th k="logi_response_median_s">Logi resp</Th>
          <Th k="damage_lead_median_s">Lead</Th>
          <Th k="deaths_flagged">Deaths</Th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.character_id} data-flagged={r.deaths_flagged > 0} data-self={r.is_self}>
            <td className="dim">{r.user_name ?? '—'}</td>
            <td style={{ fontWeight: r.is_self ? 600 : 400 }}>
              <PilotName name={r.character_name} characterId={r.character_id} />
              {r.is_self ? ' ★' : ''}
            </td>
            <td style={{ textAlign: 'center' }}>{fmtNum(r.damage_done)}</td>
            <td style={{ textAlign: 'center' }}>{fmtNum(r.reps_out)}</td>
            <td style={{ textAlign: 'center' }}>{r.kills_on}</td>
            <td style={{ textAlign: 'center' }}>{fmtS(r.target_median_switch_s)}</td>
            <td style={{ textAlign: 'center' }}>{fmtS(r.logi_response_median_s)}</td>
            <td style={{ textAlign: 'center', color: r.late_broadcasts > 0 ? '#ffb300' : undefined }}>
              {fmtS(r.damage_lead_median_s)}
              {r.late_broadcasts > 0 ? ` (${r.late_broadcasts}⚠)` : ''}
            </td>
            <td style={{ textAlign: 'center' }}>
              {r.deaths}
              {r.deaths_flagged > 0 ? ` (${r.deaths_flagged}⚠)` : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function TableLegend() {
  return (
    <p className="dim" style={{ fontSize: '0.72rem', margin: '0.25rem 0 0' }}>
      <b>Lead</b> is broadcast timing vs the damage burst (T-minus): <b>negative = called early</b>{' '}
      (proactive, better), positive = the burst was already landing (late).{' '}
      <span style={{ color: '#ffb300' }}>⚠</span> in <b>Lead</b> = count of calls sent late;{' '}
      <span style={{ color: '#ffb300' }}>⚠</span> in <b>Deaths</b> = a death with a missing,
      too-late, or unanswered broadcast.
    </p>
  )
}

interface Props {
  brId: string
  reloadKey?: number
}

export function PerformancePanel({ brId, reloadKey }: Props) {
  const [perf, setPerf] = useState<BrPerformance | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.performance(brId).then(
      (p) => { if (!cancelled) { setPerf(p); setLoading(false) } },
      (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey])

  if (loading) return <p className="dim">Loading performance…</p>
  if (error) return <p className="error-text" data-testid="performance-error">{error}</p>
  if (!perf || perf.characters.length === 0) {
    return (
      <p className="dim" data-testid="performance-empty" style={{ fontSize: '0.85rem' }}>
        No performance data available for your characters in this battle.
      </p>
    )
  }

  const self = perf.characters.filter((c) => c.is_self)
  // Role-scoped markers: only DPS (deal damage) appear on target-switch; only logi
  // (apply reps) appear on logi-response — matching the fleet spreads.
  const switchMarkers: Marker[] = self
    .filter((c) => c.target_median_switch_s !== null && c.damage_done > 0)
    .map((c) => ({
      value: c.target_median_switch_s as number,
      name: c.character_name,
      percentile: percentileFaster(c.target_median_switch_s as number, perf.distributions.target_switch_s),
    }))
  const logiMarkers: Marker[] = self
    .filter((c) => c.logi_response_median_s !== null && c.reps_out > 0)
    .map((c) => ({
      value: c.logi_response_median_s as number,
      name: c.character_name,
      percentile: percentileFaster(c.logi_response_median_s as number, perf.distributions.logi_response_s),
    }))
  // Broadcast timing is universal; T-minus convention (negative = earlier = better).
  const leadMarkers: Marker[] = self
    .filter((c) => c.damage_lead_median_s !== null)
    .map((c) => ({
      value: c.damage_lead_median_s as number,
      name: c.character_name,
      percentile: percentileFaster(c.damage_lead_median_s as number, perf.distributions.damage_lead_s),
    }))

  return (
    <div data-testid="performance-panel">
      {perf.has_broadcasts && (
        <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {perf.distributions.target_switch_s.length > 0 && (
            <MarkerHist label="DPS — target-switch vs fleet" unit="pilots" maxSec={20} binSec={1}
              population={perf.distributions.target_switch_s} markers={switchMarkers} />
          )}
          {perf.distributions.logi_response_s.length > 0 && (
            <MarkerHist label="Logi — response time vs fleet" unit="pilots" maxSec={20} binSec={1}
              population={perf.distributions.logi_response_s} markers={logiMarkers} />
          )}
          {perf.distributions.damage_lead_s.length > 0 && (
            <MarkerHist label="Broadcast timing vs fleet (◄ earlier is better)" unit="pilots" minSec={-10} maxSec={10} binSec={1}
              betterText="earlier than"
              population={perf.distributions.damage_lead_s} markers={leadMarkers} />
          )}
        </div>
      )}

      {self.length > 0 && (
        <div data-testid="my-performance">
          <h3 style={{ margin: '0.25rem 0 0.25rem', fontSize: '0.95rem' }}>My characters</h3>
          <PerfTable rows={self} sortable={false} />
          <TableLegend />
        </div>
      )}

      {perf.elevated && (
        <details data-testid="fleet-performance" style={{ marginTop: '0.75rem' }} open>
          <summary style={{ cursor: 'pointer', fontSize: '0.95rem', fontWeight: 600 }}>
            Fleet performance ({perf.characters.length}) — FC / High Command
          </summary>
          <PerfTable rows={perf.characters} sortable={true} />
          <TableLegend />
        </details>
      )}
    </div>
  )
}
