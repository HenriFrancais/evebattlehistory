// One side of the Involved tab: header with the side's totals, then one row per
// pilot from the largest hull down — or the hull composition, or (FC/HC) the
// pilots grouped by the main they belong to.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { CompositionPilot, CompositionSide } from '../api'
import { api, ApiError } from '../api'
import { useEntities } from '../entities'
import { fmtCompact, fmtIsk } from '../format'
import { groupByMain, sortPilots } from '../involved'
import { PilotName } from './PilotName'
import { ShipPicker } from './ShipPicker'

export type InvolvedMode = 'pilot' | 'ship' | 'main'

function shipIcon(id: number | null, size = 40) {
  if (id == null) return <span className="comp-ship-icon comp-ship-none" style={{ width: size, height: size }} />
  return (
    <img className="comp-ship-icon" width={size} height={size}
      src={`https://images.evetech.net/types/${id}/icon?size=64`} alt="" />
  )
}

interface RowProps {
  p: CompositionPilot
  brId: string
  sideKind: string
  showModules: boolean
  canEdit: boolean
  onChanged: () => void
}

function PilotRow({ p, brId, sideKind, showModules, canEdit, onChanged }: RowProps) {
  const entities = useEntities()
  const [logBusy, setLogBusy] = useState(false)
  const [logErr, setLogErr] = useState<string | null>(null)
  const friendly = sideKind === 'friendly'
  const tags = entities.byId(p.character_id)
  const allianceId = p.alliance_id ?? tags?.allianceId ?? null
  const corporationId = p.corporation_id ?? tags?.corporationId ?? null
  // Alliance logo, else the corporation's for a corp in no alliance.
  const logo = allianceId != null
    ? `https://images.evetech.net/alliances/${allianceId}/logo?size=64`
    : corporationId != null
      ? `https://images.evetech.net/corporations/${corporationId}/logo?size=64`
      : null
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
  const name = <PilotName name={p.character_name} characterId={p.character_id} />
  const classes = ['inv-row']
  if (p.lost) classes.push('lost')
  if (p.from_logs) classes.push('comp-from-logs')
  const weapons = p.weapons ?? []
  const killUrl = p.lost && p.killmail_id != null ? `https://zkillboard.com/kill/${p.killmail_id}/` : null
  if (killUrl) classes.push('clickable')
  // A lost row is one big link to its killmail. The controls inside it (pilot link,
  // log download, ship picker, side buttons) keep their own behaviour; the ship name
  // stays a real link so the killmail is reachable from the keyboard.
  const openKill = (e: React.MouseEvent) => {
    if (!killUrl) return
    if ((e.target as HTMLElement).closest('a, button, input, select, .ship-picker')) return
    window.open(killUrl, '_blank', 'noopener,noreferrer')
  }
  return (
    <div
      className={classes.join(' ')}
      data-testid={`pilot-${p.character_id}`}
      data-lost={p.lost || undefined}
      onClick={killUrl ? openKill : undefined}
      title={killUrl ? 'Lost — click to open the killmail on zKillboard' : undefined}
    >
      {shipIcon(p.ship_type_id)}
      {logo
        ? <img className="inv-logo" width={28} height={28} src={logo} alt="" />
        : <span className="inv-logo" />}
      <div className="inv-who">
        <div className="inv-name">
          {friendly && (
            <span
              className={`comp-log-dot ${p.has_logs ? 'comp-log-yes' : 'comp-log-no'}`}
              aria-hidden
              title={p.has_logs ? 'logs uploaded' : 'no logs uploaded'}
            >●</span>
          )}
          {/* can_download_log = this viewer may open the pilot's own log detail (FC/HC,
              or their own character): the same rule decides who they may isolate. */}
          {p.can_download_log ? (
            <Link
              className="inv-pilot-link"
              to={`/brs/${brId}/timeline?pilots=${p.character_id}`}
              title="Show this pilot on the timeline"
            >
              {name}
            </Link>
          ) : name}
          {friendly && p.can_download_log && (
            <button
              className="btn-mini inv-log-btn"
              title={logErr ?? "Download this character's gamelog for the battle (cleaned)"}
              disabled={logBusy}
              onClick={() => { void downloadLog() }}
            >
              {logBusy ? '…' : 'log'}
            </button>
          )}
        </div>
        <div className="inv-ship">
          {killUrl ? (
            <a href={killUrl} target="_blank" rel="noopener noreferrer"
              title="Lost — open the killmail on zKillboard">
              {p.ship_name}<span className="sr-only"> (lost)</span>
            </a>
          ) : (
            <span>{p.ship_name}{p.lost && <span className="sr-only"> (lost)</span>}</span>
          )}
          {p.reship && <span className="comp-reship" title="reshipped during the battle">reship</span>}
          {p.from_logs && (
            <span className="comp-from-logs-badge" data-testid="from-logs-badge"
              title="identified from logs — not on the killboard">from logs</span>
          )}
          {p.from_logs && canEdit && (
            <ShipPicker brId={brId} characterId={p.character_id}
              currentShipTypeId={p.ship_type_id} onChanged={onChanged} />
          )}
          {p.from_logs && canEdit && (
            <span className="comp-side-set" data-testid={`side-set-${p.character_id}`} title="set side for this character">
              <button className={`btn-mini side-f${friendly ? ' on' : ''}`}
                aria-pressed={friendly} onClick={() => { void setSide('friendly') }}>F</button>
              <button className={`btn-mini side-h${sideKind === 'hostile' ? ' on' : ''}`}
                aria-pressed={sideKind === 'hostile'} onClick={() => { void setSide('hostile') }}>H</button>
            </span>
          )}
        </div>
      </div>
      {showModules && (
        <div className="inv-modules" data-testid="pilot-modules">
          {weapons.map((w) => (
            <img key={w.type_id} className="inv-module" width={32} height={32} alt={w.name} title={w.name}
              src={`https://images.evetech.net/types/${w.type_id}/icon?size=64`} />
          ))}
        </div>
      )}
      <div className="inv-stats">
        {p.kill_count > 0 && (
          <span className="comp-stat comp-stat-dmg"
            title={`${p.damage_done.toLocaleString('en-US')} damage dealt across ${p.kill_count} killmail${p.kill_count === 1 ? '' : 's'}`}>
            <span className="comp-stat-icon" aria-hidden>⚔</span>
            {fmtCompact(p.damage_done)}
            <span className="comp-stat-count"> [{p.kill_count}]</span>
          </span>
        )}
        {p.reps_out > 0 && (
          <span className="comp-stat comp-stat-rep"
            title={`${Math.round(p.reps_out).toLocaleString('en-US')} HP remote-repaired onto others`}>
            <span className="comp-stat-icon" aria-hidden>✚</span>
            {fmtCompact(p.reps_out)}
          </span>
        )}
      </div>
    </div>
  )
}

function ShipView({ side }: { side: CompositionSide }) {
  const unknown = side.pilots.filter((p) => p.from_logs && p.ship_type_id == null).length
  return (
    <div>
      {side.ships.map((sh) => (
        <div className="comp-row" key={sh.ship_type_id}>
          {shipIcon(sh.ship_type_id, 34)}
          <span className="comp-count">{sh.count}×</span>
          <span className="comp-name" title={sh.ship_name}>{sh.ship_name}</span>
          <span className="comp-mod-cols" data-testid="ship-modules">
            {Array.from({ length: 5 }, (_, i) => {
              const m = (sh.top_modules ?? [])[i]
              return m ? (
                <img key={m.type_id} className="comp-item-icon" width={34} height={34}
                  src={`https://images.evetech.net/types/${m.type_id}/icon?size=64`}
                  title={m.name} alt={m.name} />
              ) : (
                <span key={`empty-${i}`} className="comp-item-icon comp-mod-empty" />
              )
            })}
          </span>
        </div>
      ))}
      {unknown > 0 && (
        <div className="comp-row comp-unknown-row" data-testid="from-logs-unknown">
          {shipIcon(null, 34)}
          <span className="comp-count">{unknown}×</span>
          <span className="comp-name dim">Unknown <span className="comp-from-logs-badge">from logs</span></span>
        </div>
      )}
    </div>
  )
}

interface Props {
  side: CompositionSide
  brId: string
  mode: InvolvedMode
  showModules: boolean
  canEdit: boolean
  onChanged: () => void
}

export function InvolvedSide({ side, brId, mode, showModules, canEdit, onChanged }: Props) {
  const cls = side.side_kind === 'friendly' ? 'friendly' : side.side_kind === 'hostile' ? 'hostile' : ''
  const losses = side.losses ?? 0
  const row = (p: CompositionPilot) => (
    <PilotRow
      key={`${p.character_id}-${p.ship_type_id}`}
      p={p} brId={brId} sideKind={side.side_kind} showModules={showModules}
      canEdit={canEdit} onChanged={onChanged}
    />
  )

  return (
    <section className={`inv-side ${cls}`} data-testid={`involved-${side.side_kind}`}>
      <header className={`comp-side-h ${cls}`}>
        <span className={`comp-side-name ${cls}`}>{side.side_kind}</span>
        <span className="inv-side-stats">
          <span>{side.pilot_count} pilot{side.pilot_count === 1 ? '' : 's'}</span>
          <span>{side.ships.length} hull{side.ships.length === 1 ? '' : 's'}</span>
          <span className={losses > 0 ? 'inv-lost' : 'dim'}>{losses} lost</span>
          <span className="dim">{fmtIsk(side.isk_lost ?? 0)} ISK</span>
        </span>
      </header>

      {mode === 'ship' && <ShipView side={side} />}
      {mode === 'pilot' && <div className="inv-rows">{sortPilots(side.pilots).map(row)}</div>}
      {mode === 'main' && groupByMain(side).map((g) => (
        <div key={g.user} className="inv-main" data-testid="inv-main">
          <div className={g.known ? 'inv-main-head' : 'inv-main-head dim'}>
            {g.user}
            <span className="inv-group-count">
              {new Set(g.pilots.map((p) => p.character_id)).size}
            </span>
          </div>
          <div className="inv-rows">{g.pilots.map(row)}</div>
        </div>
      ))}
    </section>
  )
}
