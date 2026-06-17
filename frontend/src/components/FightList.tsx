import { Link } from 'react-router-dom'
import type { FightOut, FightSideOut } from '../api'
import { fmtIsk } from '../format'

function SideChip({ side }: { side: FightSideOut }) {
  const kind = side.side_kind?.toLowerCase() ?? ''
  const cls = kind === 'us' ? 'side-chip side-us' : kind === 'them' ? 'side-chip side-them' : 'side-chip'
  return (
    <span className={cls}>
      {side.side_kind ?? `Side ${side.side_idx + 1}`} · {side.pilot_count} pilots · {fmtIsk(side.isk_lost)} lost
    </span>
  )
}

function FightRow({ fight, brId }: { fight: FightOut; brId?: string }) {
  const time = fight.started_at
    ? new Date(fight.started_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : '??:??'
  return (
    <div className="fight-card" data-testid="fight-card">
      <div className="fight-card-header">
        <span className="dim">{time}</span>
        <span>System {fight.system_id}</span>
        <span>{fmtIsk(fight.isk_destroyed_total)} ISK destroyed</span>
        {brId && (
          <Link
            to={`/brs/${brId}/fights/${fight.fight_id}`}
            className="btn"
            style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}
          >
            Detail →
          </Link>
        )}
      </div>
      <div className="sides-row">
        {fight.sides.map((s) => <SideChip key={s.side_idx} side={s} />)}
      </div>
    </div>
  )
}

interface Props {
  fights: FightOut[]
  brId?: string
}

export function FightList({ fights, brId }: Props) {
  if (fights.length === 0) return <p className="dim">No engagements recorded.</p>
  const sorted = [...fights].sort((a, b) => {
    if (a.started_at && b.started_at) return a.started_at.localeCompare(b.started_at)
    return a.fight_id - b.fight_id
  })
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {sorted.map((f) => <FightRow key={f.fight_id} fight={f} brId={brId} />)}
    </div>
  )
}
