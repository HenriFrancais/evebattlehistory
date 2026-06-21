// Snapshot: per-target breakdown for a selected time range.
//
// Rows are grouped by TARGET (the entity on the receiving end of the effect),
// then by effect family (damage / reps / cap / ewar), then listed by source.
// Because the container already encodes "who received it", direction is dropped:
// an incoming row is just flipped so its applier becomes the source.
//
// Works fleet-wide (brId) or scoped to one pilot (charId) — same layout as the
// fleet view, which is why the per-character page reuses it.
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

// effect_type → display family. The container (target) carries the meaning, so
// in/out collapse into the same family.
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

interface RowIconProps {
  effectType: string
  iconTypeId: number | null
  moduleName: string | null
}

function RowIcon({ effectType, iconTypeId, moduleName }: RowIconProps) {
  if (effectType === 'damage' && iconTypeId != null) {
    return (
      <img className="contrib-eff-icon"
        src={`https://images.evetech.net/types/${iconTypeId}/icon?size=32`}
        alt={moduleName ?? 'weapon'} title={moduleName ?? undefined} width={18} height={18} />
    )
  }
  const id = EFFECT_ICON[effectType]
  if (id == null) return <span className="contrib-eff-dot" />
  return (
    <img className="contrib-eff-icon"
      src={`https://images.evetech.net/types/${id}/icon?size=32`}
      alt={EFFECT_LABEL[effectType] ?? effectType}
      title={moduleName ?? EFFECT_LABEL[effectType] ?? effectType} width={18} height={18} />
  )
}

// One normalised source→target effect, direction folded away.
interface NormRow {
  target: string
  targetShip: string | null
  family: string
  source: string
  value: number
  effectType: string
  iconTypeId: number | null
  moduleName: string | null
  quality: string | null
}

function normalize(rows: Contribution[]): NormRow[] {
  const out: NormRow[] = []
  for (const r of rows) {
    const family = EFFECT_FAMILY[r.effect_type]
    if (!family) continue // only the four known families are shown
    const incoming = r.direction === 'in'
    out.push({
      // Incoming: the log owner (source_name) RECEIVED the effect, so they are
      // the target and the other party (target_name) is the real source.
      target: incoming ? r.source_name : r.target_name,
      targetShip: incoming ? null : r.target_ship,
      source: incoming ? r.target_name : r.source_name,
      family,
      value: r.value,
      effectType: r.effect_type,
      iconTypeId: r.icon_type_id ?? null,
      moduleName: r.module_name ?? null,
      quality: r.quality ?? null,
    })
  }
  return out
}

interface TargetGroup {
  target: string
  ship: string | null
  total: number
  byFamily: Map<string, NormRow[]>
}

function groupByTarget(rows: Contribution[]): TargetGroup[] {
  const map = new Map<string, TargetGroup>()
  for (const n of normalize(rows)) {
    let g = map.get(n.target)
    if (!g) { g = { target: n.target, ship: n.targetShip, total: 0, byFamily: new Map() }; map.set(n.target, g) }
    if (!g.ship && n.targetShip) g.ship = n.targetShip
    g.total += n.value
    const arr = g.byFamily.get(n.family)
    if (arr) arr.push(n)
    else g.byFamily.set(n.family, [n])
  }
  const groups = [...map.values()]
  for (const g of groups) {
    for (const arr of g.byFamily.values()) arr.sort((a, b) => b.value - a.value)
  }
  // Most-pummelled target first.
  groups.sort((a, b) => b.total - a.total)
  return groups
}

function TargetCard({ group }: { group: TargetGroup }) {
  const head = group.ship ? `${group.target} (${group.ship})` : group.target
  return (
    <div className="focus-card" data-testid="snap-target">
      <div className="focus-card-head" data-testid="focus-card-head" title={head}>{head}</div>
      {FAMILY_ORDER.map(({ id, label }) => {
        const rows = group.byFamily.get(id)
        if (!rows || rows.length === 0) return null // omit empty families
        const sum = rows.reduce((a, r) => a + r.value, 0)
        return (
          <div className="snap-fam" data-testid={`fam-${id}`} key={id}>
            <div className="snap-fam-head">{label} · {fmtCompact(sum)}</div>
            {rows.map((r, i) => (
              <div className="focus-row" key={i}>
                <RowIcon effectType={r.effectType} iconTypeId={r.iconTypeId} moduleName={r.moduleName} />
                <span className="focus-src" title={r.source}>{r.source}</span>
                {r.quality && <span className="focus-quality" title="dominant hit quality">{r.quality}</span>}
                <span className="focus-val">{fmtCompact(r.value)}</span>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

interface Props {
  brId: string
  range: { from: number; to: number } | null
  /** When set, scope the snapshot to this character (per-pilot view). */
  charId?: string
  /** When provided, a "Clear" button is shown in the header to clear the selected range. */
  onClearRange?: () => void
}

export function SnapshotPanel({ brId, range, charId, onClearRange }: Props) {
  const [data, setData] = useState<ContributionsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (range == null) { setData(null); return }
    setLoading(true); setError(null)
    let cancelled = false
    const from = Math.round(range.from)
    const to = Math.round(range.to)
    const handle = setTimeout(() => {
      const req = charId
        ? api.characterSnapshot(brId, charId, from, to)
        : api.snapshot(brId, from, to)
      req.then(
        (d) => { if (!cancelled) { setData(d); setLoading(false) } },
        (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
      )
    }, 120)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [brId, charId, range])

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
  const groups = groupByTarget(rows)
  const hasRows = groups.length > 0
  return (
    <div className="contrib-panel" data-testid="fleet-contrib">
      <div className="contrib-head">
        <strong>{fmtTime(range.from, true)} → {fmtTime(range.to, true)} UTC</strong>
        {onClearRange && (
          <button type="button" className="btn-mini" data-testid="snap-clear-btn" onClick={onClearRange}>
            Clear
          </button>
        )}
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
        {groups.map((g) => <TargetCard key={g.target} group={g} />)}
      </div>
    </div>
  )
}
