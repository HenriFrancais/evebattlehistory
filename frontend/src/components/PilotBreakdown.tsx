// Expanded stats-table row: who one pilot hit (or was hit by) in the selected
// range, per weapon / module, with the single-hit range and hit-quality mix.
// The only part of the timeline tab that asks the server: per-target and
// per-weapon detail is too large to preload for every pilot.
import { useEffect, useState } from 'react'
import type { Contribution } from '../api'
import { api } from '../api'
import { fmtCompact } from '../format'
import type { StatFamily } from '../pilotStats'
import { FAMILY_EFFECTS } from '../pilotStats'
import { PilotName } from './PilotName'

const EFFECT_LABEL: Record<string, string> = {
  damage: 'Damage', rep_armor: 'Armor rep', rep_shield: 'Shield rep', neut: 'Neut', nos: 'Nos',
  cap_transfer: 'Cap transfer', scram: 'Scram', disrupt: 'Point', jam: 'Jam',
}
// Best to worst, then misses.
const QUALITY_ORDER = [
  'Wrecks', 'Smashes', 'Penetrates', 'Hits', 'Glances Off', 'Grazes', 'Barely Scratches',
  'Deflected', 'Misses',
]
// Effects the backend reports from the log owner's side with a true direction. The
// others (reps, cap transfer, tackle) come back as canonical source → target rows.
const OWNER_SIDED = new Set(['damage', 'neut', 'nos', 'jam'])

export interface BreakdownRow {
  other: string
  ship: string | null
  effectType: string
  moduleName: string | null
  iconTypeId: number | null
  value: number
  hits: number
  min: number | null
  max: number | null
  qualities: [string, number][]
}

/** The rows of one pilot's snapshot that belong to a stat type and direction. */
export function breakdownRows(
  rows: Contribution[], pilotName: string, family: StatFamily, direction: 'out' | 'in',
): BreakdownRow[] {
  const effects = new Set(FAMILY_EFFECTS[family])
  const me = pilotName.toLowerCase()
  const out: BreakdownRow[] = []
  for (const r of rows) {
    if (!effects.has(r.effect_type)) continue
    let dir: string
    let other: string
    if (OWNER_SIDED.has(r.effect_type)) {
      dir = r.direction
      other = r.target_name
    } else {
      const applied = r.source_name.toLowerCase() === me
      dir = applied ? 'out' : 'in'
      other = applied ? r.target_name : r.source_name
    }
    if (dir !== direction) continue
    const counts = r.quality_counts ?? {}
    out.push({
      other,
      ship: OWNER_SIDED.has(r.effect_type) || dir === 'out' ? r.target_ship : null,
      effectType: r.effect_type,
      moduleName: r.module_name,
      iconTypeId: r.icon_type_id,
      value: r.value,
      hits: r.hits ?? 0,
      min: r.min_hit ?? null,
      max: r.max_hit ?? null,
      qualities: QUALITY_ORDER.filter((q) => counts[q]).map((q) => [q, counts[q]]),
    })
  }
  return out.sort((a, b) => b.value - a.value)
}

interface Props {
  brId: string
  characterId: number
  pilotName: string
  from: number
  to: number
  family: StatFamily
  direction: 'out' | 'in'
}

export function PilotBreakdown({ brId, characterId, pilotName, from, to, family, direction }: Props) {
  const [rows, setRows] = useState<Contribution[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fromTs = Math.floor(from)
  const toTs = Math.ceil(to)

  useEffect(() => {
    let cancelled = false
    setError(null)
    // Debounced: the range changes continuously while a handle is dragged.
    const handle = setTimeout(() => {
      api.characterSnapshot(brId, String(characterId), fromTs, toTs).then(
        (d) => { if (!cancelled) setRows(d.rows) },
        (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
      )
    }, 150)
    return () => { cancelled = true; clearTimeout(handle) }
  }, [brId, characterId, fromTs, toTs])

  if (error) return <p className="error-text" role="alert">Could not load the breakdown: {error}</p>
  if (rows == null) return <p className="dim">Loading breakdown…</p>

  const shown = breakdownRows(rows, pilotName, family, direction)
  if (shown.length === 0) {
    return <p className="dim">Nothing logged against a named target in this range.</p>
  }
  const isCount = family === 'ewar'
  return (
    <table className="stats-breakdown" data-testid={`breakdown-${characterId}`}>
      <thead>
        <tr>
          <th>{direction === 'out' ? 'Target' : 'Source'}</th>
          <th>{family === 'damage' ? 'Weapon' : 'Module'}</th>
          <th className="num">Total</th>
          <th className="num">{family === 'damage' ? 'Hits' : isCount ? 'Applications' : 'Cycles'}</th>
          {!isCount && <th className="num">Min</th>}
          {!isCount && <th className="num">Max</th>}
          {family === 'damage' && <th>Hit quality</th>}
        </tr>
      </thead>
      <tbody>
        {shown.map((r, i) => (
          <tr key={i}>
            <td>
              <PilotName name={r.other} />
              {r.ship && <span className="dim"> · {r.ship}</span>}
            </td>
            <td>
              {r.iconTypeId != null && (
                <img
                  className="contrib-eff-icon" width={18} height={18} alt=""
                  src={`https://images.evetech.net/types/${r.iconTypeId}/icon?size=32`}
                />
              )}
              {r.moduleName ?? EFFECT_LABEL[r.effectType] ?? r.effectType}
            </td>
            <td className="num">{fmtCompact(r.value)}</td>
            <td className="num">{r.hits}</td>
            {!isCount && <td className="num">{r.min == null ? <Blank /> : fmtCompact(r.min)}</td>}
            {!isCount && <td className="num">{r.max == null ? <Blank /> : fmtCompact(r.max)}</td>}
            {family === 'damage' && (
              <td className="stats-quality">
                {r.qualities.map(([q, n]) => (
                  <span key={q} className={q === 'Misses' ? 'q-miss' : undefined}>{q} {n}</span>
                ))}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Blank() {
  return <span className="stats-blank" aria-label="not recorded">–</span>
}
