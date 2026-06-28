// Fleet composition summary with Composition / By-character / By-user modes.
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CompositionPilot, CompositionResponse, CompositionSide } from '../api'
import { api, ApiError } from '../api'
import { loadComposition } from '../cache'
import { fmtCompact } from '../format'
import { ShipPicker } from './ShipPicker'

type Mode = 'composition' | 'character' | 'user'

function shipIcon(id: number | null, size = 30) {
  if (id == null) return <span className="comp-ship-icon comp-ship-none" style={{ width: size, height: size }} />
  return (
    <img className="comp-ship-icon" width={size} height={size}
      src={`https://images.evetech.net/types/${id}/icon?size=32`} alt="" />
  )
}

function itemIcon(typeId: number) {
  return (
    <img
      className="comp-item-icon"
      width={26}
      height={26}
      src={`https://images.evetech.net/types/${typeId}/icon?size=64`}
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
      <span className="dim" style={{ fontSize: '0.95rem' }}>{side.pilot_count} pilots · {hulls} hulls</span>
    </div>
  )
}

function CompositionView({ side }: { side: CompositionSide }) {
  return (
    <div>
      <SideHeader side={side} />
      {side.ships.map((sh) => (
        <div className="comp-row" key={sh.ship_type_id}>
          {shipIcon(sh.ship_type_id, 34)}
          <span className="comp-count">{sh.count}×</span>
          <span className="comp-name" title={sh.ship_name}>{sh.ship_name}</span>
          <span className="comp-mod-cols" data-testid="ship-modules">
            {Array.from({ length: 5 }, (_, i) => {
              const m = (sh.top_modules ?? [])[i]
              return m ? (
                <img
                  key={m.type_id}
                  className="comp-item-icon"
                  width={34}
                  height={34}
                  src={`https://images.evetech.net/types/${m.type_id}/icon?size=64`}
                  title={m.name}
                  alt={m.name}
                />
              ) : (
                <span key={`empty-${i}`} className="comp-item-icon comp-mod-empty" />
              )
            })}
          </span>
        </div>
      ))}
      {(() => {
        const unknown = side.pilots.filter((p) => p.from_logs && p.ship_type_id == null).length
        return unknown > 0 ? (
          <div className="comp-row comp-unknown-row" data-testid="from-logs-unknown">
            {shipIcon(null, 34)}
            <span className="comp-count">{unknown}×</span>
            <span className="comp-name dim">Unknown <span className="comp-from-logs-badge">from logs</span></span>
          </div>
        ) : null
      })()}
    </div>
  )
}

function PilotRow({ p, showWeapons, showLogs, brId, canEdit, onChanged, sideKind }: { p: CompositionPilot; showWeapons: boolean; showLogs: boolean; brId: string; canEdit: boolean; onChanged: () => void; sideKind: string }) {
  const weapons = p.weapons ?? []
  const [logBusy, setLogBusy] = useState(false)
  const [logErr, setLogErr] = useState<string | null>(null)
  const setSide = (side: 'friendly' | 'hostile') =>
    api.setParticipantSide(brId, p.character_id, sideKind === side ? null : side).then(onChanged)
  const downloadLog = async () => {
    setLogBusy(true)
    setLogErr(null)
    try {
      const { blob, filename } = await api.downloadCharacterLog(brId, p.character_id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setLogErr(e instanceof ApiError ? e.message : 'download failed')
    } finally {
      setLogBusy(false)
    }
  }
  return (
    <div className={p.from_logs ? 'comp-from-logs' : undefined}>
      <div className="comp-prow">
        {shipIcon(p.ship_type_id, 34)}
        <span className="comp-name" title={p.character_name}>
          {showLogs && (
            <span
              className={`comp-log-dot ${p.has_logs ? 'comp-log-yes' : 'comp-log-no'}`}
              aria-hidden
              title={p.has_logs ? 'logs uploaded' : 'no logs uploaded'}
            >●</span>
          )}
          {p.character_name}
          {p.lost && p.killmail_id != null ? (
            <a className="comp-lost" href={`https://zkillboard.com/kill/${p.killmail_id}/`}
               target="_blank" rel="noopener noreferrer" title="lost ship — open on zKillboard"
               aria-label="lost ship"> ✗</a>
          ) : p.lost ? (
            <span className="comp-lost" title="lost ship"> ✗</span>
          ) : null}
          {showLogs && p.has_logs && (
            <button
              className="btn-mini"
              style={{ marginLeft: '0.3rem', fontSize: '0.72rem', padding: '0.05rem 0.3rem' }}
              title={logErr ?? "Download this character's gamelog for the battle (cleaned)"}
              disabled={logBusy}
              onClick={downloadLog}
            >
              {logBusy ? '…' : 'log'}
            </button>
          )}
        </span>
        {p.kill_count > 0 ? (
          <span className="comp-stat comp-stat-dmg"
            title={`${p.damage_done.toLocaleString()} damage dealt across ${p.kill_count} killmail${p.kill_count === 1 ? '' : 's'}`}>
            <span className="comp-stat-icon" aria-hidden>⚔</span>
            {fmtCompact(p.damage_done)}
            <span className="comp-stat-count"> [{p.kill_count}]</span>
          </span>
        ) : <span className="comp-stat comp-stat-dmg" />}
        <span className="comp-line2">
          <span className="dim comp-ship-sub">{p.ship_name}</span>
          {p.reship && <span className="comp-reship" title="reshipped during the battle">↻ reship</span>}
          {p.from_logs && (
            <span className="comp-from-logs-badge" data-testid="from-logs-badge"
              title="identified from logs — not on the killboard">📋 from logs</span>
          )}
          {p.from_logs && canEdit && (
            <ShipPicker brId={brId} characterId={p.character_id}
              currentShipTypeId={p.ship_type_id} onChanged={onChanged} />
          )}
          {p.from_logs && canEdit && (
            <span className="comp-side-set" data-testid={`side-set-${p.character_id}`} title="set side for this character">
              <button className={`btn-mini side-f${sideKind === 'friendly' ? ' on' : ''}`}
                aria-pressed={sideKind === 'friendly'} onClick={() => setSide('friendly')}>F</button>
              <button className={`btn-mini side-h${sideKind === 'hostile' ? ' on' : ''}`}
                aria-pressed={sideKind === 'hostile'} onClick={() => setSide('hostile')}>H</button>
            </span>
          )}
        </span>
        {p.reps_out > 0 ? (
          <span className="comp-stat comp-stat-rep"
            title={`${Math.round(p.reps_out).toLocaleString()} HP remote-repaired onto others`}>
            <span className="comp-stat-icon" aria-hidden>✚</span>
            {fmtCompact(p.reps_out)}
          </span>
        ) : <span className="comp-stat comp-stat-rep" />}
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

function CharacterView({ side, showWeapons, brId, canEdit, onChanged }: { side: CompositionSide; showWeapons: boolean; brId: string; canEdit: boolean; onChanged: () => void }) {
  const showLogs = side.side_kind === 'friendly'
  return (
    <div>
      <SideHeader side={side} />
      {side.pilots.map((p) => (
        <PilotRow
          key={`${p.character_id}-${p.ship_type_id}`}
          p={p}
          showWeapons={showWeapons}
          showLogs={showLogs}
          brId={brId}
          canEdit={canEdit}
          onChanged={onChanged}
          sideKind={side.side_kind}
        />
      ))}
    </div>
  )
}

function UserView({ side, showWeapons, brId, canEdit, onChanged }: { side: CompositionSide; showWeapons: boolean; brId: string; canEdit: boolean; onChanged: () => void }) {
  const groups = useMemo(() => {
    const m = new Map<string, CompositionPilot[]>()
    for (const p of side.pilots) {
      const key = p.user_name ?? 'Unmatched'
      if (!m.has(key)) m.set(key, [])
      m.get(key)!.push(p)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [side.pilots])
  const showLogs = side.side_kind === 'friendly'
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
              showWeapons={showWeapons}
              showLogs={showLogs}
              brId={brId}
              canEdit={canEdit}
              onChanged={onChanged}
              sideKind={side.side_kind}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function FleetsPanel({ brId, reloadKey }: { brId: string; reloadKey?: number }) {
  const [data, setData] = useState<CompositionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('composition')
  const [showWeapons, setShowWeapons] = useState(false)
  const [localReload, setLocalReload] = useState(0)
  // Same brId re-run ⇒ only a reload signal changed (sides / participant edit) ⇒
  // force a fresh fetch; a new brId (or first mount) reads the prefetch cache.
  const fetchedBrId = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    const force = fetchedBrId.current === brId
    fetchedBrId.current = brId
    loadComposition(brId, force).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey, localReload])

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
              onClick={() => setMode('character')}>By character</button>
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
            {mode === 'character' && <CharacterView side={side} showWeapons={showWeapons} brId={brId} canEdit={data.by_user_available} onChanged={() => setLocalReload((v) => v + 1)} />}
            {mode === 'user' && <UserView side={side} showWeapons={showWeapons} brId={brId} canEdit={data.by_user_available} onChanged={() => setLocalReload((v) => v + 1)} />}
          </div>
        ))}
      </div>
    </div>
  )
}
