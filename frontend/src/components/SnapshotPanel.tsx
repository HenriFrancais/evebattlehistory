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
      {group.rows.map((r, i) => (
        <div className="focus-row" key={i}>
          <RowIcon row={r} />
          <span className="focus-src" title={r.source_name}>{r.source_name}</span>
          {r.quality && <span className="focus-quality" title="dominant hit quality">{r.quality}</span>}
          <span className="dim focus-dir">{r.direction === 'in' ? '←' : '→'}</span>
          <span className="focus-val">{fmtCompact(r.value)}</span>
        </div>
      ))}
    </div>
  )
}

// Group effect types into the four display families.
const EFFECT_FAMILY: Record<string, string> = {
  damage: 'damage',
  rep_armor: 'reps', rep_shield: 'reps',
  neut: 'cap', nos: 'cap', cap_transfer: 'cap',
  scram: 'ewar', disrupt: 'ewar', jam: 'ewar',
}
const FAMILY_ORDER: { id: string; label: string }[] = [
  { id: 'damage', label: 'Damage' }, { id: 'reps', label: 'Reps' },
  { id: 'cap', label: 'Cap' }, { id: 'ewar', label: 'EWAR' },
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
  const hasRows = rows.length > 0
  return (
    <div className="contrib-panel" data-testid="fleet-contrib">
      <div className="contrib-head">
        <strong>{fmtTime(range.from, true)} → {fmtTime(range.to, true)} UTC</strong>
      </div>
      {loading && rows.length === 0 && <p className="dim">Loading…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && !error && !hasRows && (
        <p className="dim" style={{ fontSize: '0.78rem' }}>
          {range.from === range.to
            ? 'Pick an end point on the graph.'
            : 'No logged activity in this window.'}
        </p>
      )}
      <div className="focus-list">
        {FAMILY_ORDER.map(({ id, label }) => {
          const famRows = rows.filter((r) => EFFECT_FAMILY[r.effect_type] === id)
          if (famRows.length === 0) return null
          const targets = groupByTarget(famRows)
          const sum = famRows.reduce((a, r) => a + r.value, 0)
          return (
            <div className="snap-cluster" data-testid={`cluster-${id}`} key={id} tabIndex={0}>
              <div className="snap-cluster-head">
                {label} · {targets.length} {targets.length === 1 ? 'target' : 'targets'} · {fmtCompact(sum)}
              </div>
              <div className="snap-cluster-body">
                {targets.map((g) => <TargetCard key={`${g.target}-${g.ship ?? ''}`} group={g} />)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
