// Fleet composition summary with Composition / Per-character / By-user modes.
import { useEffect, useMemo, useState } from 'react'
import type { CompositionPilot, CompositionResponse, CompositionSide } from '../api'
import { api } from '../api'

type Mode = 'composition' | 'character' | 'user'

function shipIcon(id: number | null, size = 30) {
  if (id == null) return <span className="comp-ship-icon comp-ship-none" />
  return (
    <img className="comp-ship-icon" width={size} height={size}
      src={`https://images.evetech.net/types/${id}/icon?size=32`} alt="" />
  )
}

function itemIcon(typeId: number) {
  return (
    <img
      className="comp-item-icon"
      width={20}
      height={20}
      src={`https://images.evetech.net/types/${typeId}/icon?size=32`}
      alt=""
    />
  )
}

function SideHeader({ side }: { side: CompositionSide }) {
  const hulls = side.ships.length
  const cls = side.side_kind === 'friendly' ? 'friendly' : side.side_kind === 'hostile' ? 'hostile' : ''
  return (
    <div className={`comp-side-h ${cls}`}>
      <span className={`comp-side-name ${cls}`}>{side.side_kind}</span>
      <span className="dim" style={{ fontSize: '0.74rem' }}>{side.pilot_count} pilots · {hulls} hulls</span>
    </div>
  )
}

function CompositionView({ side }: { side: CompositionSide }) {
  return (
    <div>
      <SideHeader side={side} />
      {side.ships.map((sh) => (
        <div className="comp-row" key={sh.ship_type_id}>
          {shipIcon(sh.ship_type_id)}
          <span className="comp-count">{sh.count}×</span>
          <span className="comp-name" title={sh.ship_name}>{sh.ship_name}</span>
        </div>
      ))}
    </div>
  )
}

function PilotRow({ p, onSelectKill, showWeapons }: { p: CompositionPilot; onSelectKill?: (kmId: number) => void; showWeapons: boolean }) {
  const weapons = p.weapons ?? []
  return (
    <div>
      <div className="comp-row">
        {shipIcon(p.ship_type_id, 18)}
        <span className="comp-name" title={p.character_name}>
          {p.character_name}
          {p.lost && p.killmail_id != null ? (
            <>
              <a className="comp-lost" href={`https://zkillboard.com/kill/${p.killmail_id}/`}
                 target="_blank" rel="noopener noreferrer" title="lost ship — open on zKillboard"
                 aria-label="lost ship"> ✗</a>
              {onSelectKill && (
                <button
                  className="btn-mini"
                  style={{ marginLeft: '0.3rem', fontSize: '0.72rem', padding: '0.05rem 0.3rem' }}
                  title="View loss detail"
                  onClick={() => onSelectKill(p.killmail_id!)}
                >
                  detail
                </button>
              )}
            </>
          ) : p.lost ? (
            <span className="comp-lost" title="lost ship"> ✗</span>
          ) : null}
        </span>
        <span className="dim comp-ship-sub">{p.ship_name}</span>
        {p.reship && <span className="comp-reship" title="reshipped during the battle">↻ reship</span>}
      </div>
      {showWeapons && weapons.length > 0 && (
        <div className="comp-pilot-modules" data-testid="pilot-modules">
          {weapons.map((w) => (
            <div key={w.type_id} className="comp-module-row" data-testid="module-row">
              {itemIcon(w.type_id)}
              <span className="comp-module-name">{w.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CharacterView({ side, onSelectKill, showWeapons }: { side: CompositionSide; onSelectKill?: (kmId: number) => void; showWeapons: boolean }) {
  return (
    <div>
      <SideHeader side={side} />
      {side.pilots.map((p) => (
        <PilotRow
          key={`${p.character_id}-${p.ship_type_id}`}
          p={p}
          onSelectKill={onSelectKill}
          showWeapons={showWeapons}
        />
      ))}
    </div>
  )
}

function UserView({ side, onSelectKill, showWeapons }: { side: CompositionSide; onSelectKill?: (kmId: number) => void; showWeapons: boolean }) {
  const groups = useMemo(() => {
    const m = new Map<string, CompositionPilot[]>()
    for (const p of side.pilots) {
      const key = p.user_name ?? 'Unmatched'
      if (!m.has(key)) m.set(key, [])
      m.get(key)!.push(p)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [side.pilots])
  return (
    <div>
      <SideHeader side={side} />
      {groups.map(([user, pilots]) => (
        <div key={user} className="comp-user-group">
          <div className="comp-user-head">▸ {user}</div>
          {pilots.map((p) => (
            <PilotRow
              key={`${p.character_id}-${p.ship_type_id}`}
              p={p}
              onSelectKill={onSelectKill}
              showWeapons={showWeapons}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function FleetsPanel({ brId, reloadKey, onSelectKill }: { brId: string; reloadKey?: number; onSelectKill?: (kmId: number) => void }) {
  const [data, setData] = useState<CompositionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('composition')
  const [showWeapons, setShowWeapons] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError(null)
    api.composition(brId).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey])

  // If By-user becomes unavailable while selected, fall back to composition.
  useEffect(() => {
    if (mode === 'user' && data && !data.by_user_available) setMode('composition')
  }, [mode, data])

  if (error) return <p className="error-text" data-testid="fleets-error">{error}</p>
  if (!data) return <p className="dim">Loading fleets…</p>
  if (data.sides.length === 0) return <p className="dim" data-testid="fleets-empty">No fleet data.</p>

  const showWeaponsToggle = mode === 'character' || mode === 'user'

  return (
    <div data-testid="fleets-panel">
      <div className="fleets-head">
        <h2 style={{ margin: 0 }}>Fleets</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <div className="seg" role="group" aria-label="Fleet view mode">
            <button className={mode === 'composition' ? 'on' : ''} aria-pressed={mode === 'composition'}
              onClick={() => setMode('composition')}>Composition</button>
            <button className={mode === 'character' ? 'on' : ''} aria-pressed={mode === 'character'}
              onClick={() => setMode('character')}>Per-character</button>
            {data.by_user_available && (
              <button className={mode === 'user' ? 'on' : ''} aria-pressed={mode === 'user'}
                onClick={() => setMode('user')}>By user</button>
            )}
          </div>
          {showWeaponsToggle && (
            <button
              className={`btn-mini${showWeapons ? ' on' : ''}`}
              aria-pressed={showWeapons}
              data-testid="toggle-modules-btn"
              onClick={() => setShowWeapons((v) => !v)}
              title={showWeapons ? 'Hide modules' : 'Show modules'}
            >
              {showWeapons ? '▲ modules' : '▼ modules'}
            </button>
          )}
        </div>
      </div>
      <div className="comp-twoside">
        {data.sides.map((side) => (
          <div key={side.side_kind}>
            {mode === 'composition' && <CompositionView side={side} />}
            {mode === 'character' && <CharacterView side={side} onSelectKill={onSelectKill} showWeapons={showWeapons} />}
            {mode === 'user' && <UserView side={side} onSelectKill={onSelectKill} showWeapons={showWeapons} />}
          </div>
        ))}
      </div>
    </div>
  )
}
