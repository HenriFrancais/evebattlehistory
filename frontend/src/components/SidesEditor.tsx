// Per-BR side configuration (FC/HC). Three columns — Friendly · Unassigned ·
// Hostile — with arrows to move an entity between adjacent columns. Edits
// persist and bubble up via onChange so the fleet graph re-fetches.

import { useEffect, useState } from 'react'
import type { BrSides, SideEntity, SideKind } from '../api'
import { api } from '../api'

const COLUMNS: { side: SideKind; title: string; accent: string }[] = [
  { side: 'friendly', title: 'Friendly', accent: 'var(--ok)' },
  { side: 'unassigned', title: 'Unassigned', accent: 'var(--text-dim)' },
  { side: 'hostile', title: 'Hostile', accent: 'var(--bad)' },
]

// Adjacent move targets per current side (left = toward friendly).
const MOVE: Record<SideKind, { left: SideKind | null; right: SideKind | null }> = {
  friendly: { left: null, right: 'unassigned' },
  unassigned: { left: 'friendly', right: 'hostile' },
  hostile: { left: 'unassigned', right: null },
}

function EntityRow({
  ent,
  canEdit,
  busy,
  onMove,
}: {
  ent: SideEntity
  canEdit: boolean
  busy: boolean
  onMove: (to: SideKind) => void
}) {
  const moves = MOVE[ent.side]
  return (
    <div className="side-entity-row">
      {canEdit && moves.left ? (
        <button className="btn-mini" disabled={busy} title={`Move to ${moves.left}`} onClick={() => onMove(moves.left!)}>
          ◄
        </button>
      ) : (
        <span className="side-arrow-spacer" />
      )}
      <span className="side-entity-name">{ent.name}</span>
      {canEdit && moves.right ? (
        <button className="btn-mini" disabled={busy} title={`Move to ${moves.right}`} onClick={() => onMove(moves.right!)}>
          ►
        </button>
      ) : (
        <span className="side-arrow-spacer" />
      )}
    </div>
  )
}

export function SidesEditor({ brId, onChange }: { brId: string; onChange?: () => void }) {
  const [sides, setSides] = useState<BrSides | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.getSides(brId).then(
      (s) => !cancelled && setSides(s),
      (e: unknown) => !cancelled && setError(String((e as Error)?.message ?? e)),
    )
    return () => {
      cancelled = true
    }
  }, [brId])

  const move = (ent: SideEntity, to: SideKind) => {
    setBusyId(ent.entity_id)
    api.setSide(brId, { entity_type: ent.entity_type, entity_id: ent.entity_id, side: to }).then(
      (s) => {
        setSides(s)
        setBusyId(null)
        onChange?.()
      },
      (e: unknown) => {
        setError(String((e as Error)?.message ?? e))
        setBusyId(null)
      },
    )
  }

  if (error) return <p className="error-text" data-testid="sides-error">{error}</p>
  if (!sides) return <p className="dim">Loading sides…</p>

  return (
    <div data-testid="sides-editor">
      {!sides.can_edit && (
        <p className="dim" style={{ fontSize: '0.82rem', margin: '0 0 0.5rem' }}>
          Side assignment is managed by FC / High Command.
        </p>
      )}
      <div className="sides-columns">
        {COLUMNS.map((col) => {
          const entities = sides.entities.filter((e) => e.side === col.side)
          return (
            <div key={col.side} className="side-column" style={{ borderTopColor: col.accent }}>
              <div className="side-column-head" style={{ color: col.accent }}>
                {col.title} <span className="dim">({entities.length})</span>
              </div>
              {entities.length === 0 && <p className="dim" style={{ fontSize: '0.8rem' }}>none</p>}
              {entities.map((e) => (
                <EntityRow
                  key={`${e.entity_type}:${e.entity_id}`}
                  ent={e}
                  canEdit={sides.can_edit}
                  busy={busyId === e.entity_id}
                  onMove={(to) => move(e, to)}
                />
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}
