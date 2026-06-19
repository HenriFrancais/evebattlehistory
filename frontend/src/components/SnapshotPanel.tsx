// Snapshot: source→target breakdown for a selected time range.
import { useEffect, useState } from 'react'
import type { Contribution, ContributionsResponse } from '../api'
import { api } from '../api'
import { fmtCompact, fmtTime } from '../format'

const EFFECT_ICON: Record<string, number> = {
  damage: 485, rep_armor: 11355, rep_shield: 3586, neut: 533, nos: 530,
  cap_transfer: 529, scram: 447, disrupt: 3242, jam: 1957,
}
const EFFECT_LABEL: Record<string, string> = {
  damage: 'damage', rep_armor: 'armor rep', rep_shield: 'shield rep', neut: 'neut',
  nos: 'nos', cap_transfer: 'cap', scram: 'scram', disrupt: 'point', jam: 'jam',
}

function RowIcon({ row }: { row: Contribution }) {
  if (row.effect_type === 'damage' && row.icon_type_id != null) {
    return (
      <img className="contrib-eff-icon"
        src={`https://images.evetech.net/types/${row.icon_type_id}/icon?size=32`}
        alt={row.module_name ?? 'weapon'} title={row.module_name ?? undefined} width={18} height={18} />
    )
  }
  const id = EFFECT_ICON[row.effect_type]
  if (id == null) return <span className="contrib-eff-dot" />
  return (
    <img className="contrib-eff-icon"
      src={`https://images.evetech.net/types/${id}/icon?size=32`}
      alt={EFFECT_LABEL[row.effect_type] ?? row.effect_type}
      title={row.module_name ?? EFFECT_LABEL[row.effect_type] ?? row.effect_type} width={18} height={18} />
  )
}

interface TargetGroup { target: string; ship: string | null; total: number; rows: Contribution[] }

function groupByTarget(rows: Contribution[]): TargetGroup[] {
  const map = new Map<string, TargetGroup>()
  for (const r of rows) {
    const key = `${r.target_name} ${r.target_ship ?? ''}`
    let g = map.get(key)
    if (!g) { g = { target: r.target_name, ship: r.target_ship, total: 0, rows: [] }; map.set(key, g) }
    g.total += r.value
    g.rows.push(r)
  }
  const groups = [...map.values()]
  for (const g of groups) g.rows.sort((a, b) => b.value - a.value)
  // Busiest targets (most effect rows) first; single-source effects sink to the bottom.
  groups.sort((a, b) => b.rows.length - a.rows.length || b.total - a.total)
  return groups
}

function TargetCard({ group }: { group: TargetGroup }) {
  const head = group.ship ? `${group.target} (${group.ship})` : group.target
  return (
    <div className="focus-card">
      <div className="focus-card-head" data-testid="focus-card-head" title={head}>{head}</div>
      {group.rows.slice(0, 12).map((r, i) => (
        <div className="focus-row" key={i}>
          <RowIcon row={r} />
          <span className="focus-src" title={r.source_name}>{r.source_name}</span>
          {r.quality && <span className="focus-quality" title="dominant hit quality">{r.quality}</span>}
          <span className="dim focus-dir">{r.direction === 'in' ? '←' : '→'}</span>
          <span className="focus-val">{fmtCompact(r.value)}</span>
        </div>
      ))}
      {group.rows.length > 12 && (
        <div className="dim" style={{ fontSize: '0.7rem' }}>+{group.rows.length - 12} more…</div>
      )}
    </div>
  )
}

const GROUP_TOTALS = [
  { id: 'damage', label: 'Dmg/Rep' },
  { id: 'cap', label: 'Cap' },
  { id: 'ewar', label: 'EWAR' },
]

export function SnapshotPanel({ brId, range }: { brId: string; range: { from: number; to: number } | null }) {
  const [data, setData] = useState<ContributionsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (range == null) { setData(null); return }
    setLoading(true); setError(null)
    let cancelled = false
    const handle = setTimeout(() => {
      api.snapshot(brId, Math.round(range.from), Math.round(range.to)).then(
        (d) => { if (!cancelled) { setData(d); setLoading(false) } },
        (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
      )
    }, 120)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [brId, range])

  if (range == null) {
    return (
      <div className="contrib-panel" data-testid="moment-detail-empty">
        <p className="dim" style={{ fontSize: '0.8rem', textAlign: 'center', padding: '1rem 0' }}>
          Shift-drag a range on any graph to snapshot it.
        </p>
      </div>
    )
  }

  const rows = data?.rows ?? []
  const targets = groupByTarget(rows)
  return (
    <div className="contrib-panel" data-testid="fleet-contrib">
      <div className="contrib-head">
        <strong>{fmtTime(range.from, true)} → {fmtTime(range.to, true)} UTC</strong>
      </div>
      <div className="focus-totals">
        {GROUP_TOTALS.map((g) => {
          const sum = rows.filter((r) => r.group === g.id).reduce((a, r) => a + r.value, 0)
          return (
            <span key={g.id} className="focus-total">
              <span className="dim">{g.label}</span> {fmtCompact(sum)}
            </span>
          )
        })}
      </div>
      {loading && rows.length === 0 && <p className="dim">Loading…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && targets.length === 0 && (
        <p className="dim" style={{ fontSize: '0.78rem' }}>
          {range.from === range.to
            ? 'Pick an end point on the graph.'
            : 'No logged activity in this window.'}
        </p>
      )}
      <div className="focus-list">
        {targets.map((g) => <TargetCard key={`${g.target}-${g.ship ?? ''}`} group={g} />)}
      </div>
    </div>
  )
}
