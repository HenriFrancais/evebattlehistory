// Inline ship-type picker for FC/HC to assign a hull to an off-BR (from-logs)
// participant. Searches SDE ship types and PUTs the per-character override.
import { useState } from 'react'
import { api, type ShipType } from '../api'

export function ShipPicker({
  brId,
  characterId,
  currentShipTypeId,
  onChanged,
}: {
  brId: string
  characterId: number
  currentShipTypeId: number | null
  onChanged: () => void
}) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<ShipType[]>([])
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  function search(v: string) {
    setQ(v)
    if (v.trim().length < 2) {
      setResults([])
      return
    }
    api.searchShipTypes(v).then((r) => setResults(r), () => setResults([]))
  }

  function pick(typeId: number | null) {
    setBusy(true)
    api.setParticipantShip(brId, characterId, typeId).then(
      () => {
        setOpen(false)
        setQ('')
        setResults([])
        setBusy(false)
        onChanged()
      },
      () => setBusy(false),
    )
  }

  if (!open) {
    return (
      <button
        className="btn-mini comp-ship-set"
        data-testid={`ship-picker-${characterId}`}
        disabled={busy}
        onClick={() => setOpen(true)}
      >
        {currentShipTypeId == null ? 'set ship' : 'change ship'}
      </button>
    )
  }

  return (
    <span className="ship-picker" data-testid={`ship-picker-${characterId}`}>
      <input
        autoFocus
        className="ship-picker-input"
        placeholder="Search ship…"
        value={q}
        onChange={(e) => search(e.target.value)}
      />
      {currentShipTypeId != null && (
        <button className="btn-mini" onClick={() => pick(null)}>clear</button>
      )}
      <button
        className="btn-mini"
        aria-label="cancel"
        onClick={() => { setOpen(false); setQ(''); setResults([]) }}
      >×</button>
      {results.length > 0 && (
        <div className="ship-picker-results">
          {results.map((s) => (
            <button
              key={s.type_id}
              className="ship-picker-opt"
              onClick={() => pick(s.type_id)}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
