import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { BrDetail, FightOut } from '../api'
import { api } from '../api'
import { fmtIsk } from '../format'

export function FightDetailPage() {
  const { id, fid } = useParams<{ id: string; fid: string }>()
  const [fight, setFight] = useState<FightOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id || !fid) return
    let cancelled = false
    api.getBr(id).then(
      (br: BrDetail) => {
        if (cancelled) return
        const f = br.fights.find((x) => String(x.fight_id) === fid)
        if (f) setFight(f)
        else setError(`Fight ${fid} not found in BR ${id}`)
      },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id, fid])

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!fight) return <div className="page"><p className="dim">Loading…</p></div>

  return (
    <div className="page">
      <div>
        <Link to={`/brs/${id}`} className="dim" style={{ fontSize: '0.85rem' }}>← BR Summary</Link>
        <h1 style={{ margin: '0.25rem 0 0' }}>Fight Detail</h1>
      </div>
      <div className="panel">
        <div style={{ marginBottom: '0.5rem' }}>
          <span className="dim">System {fight.system_id}</span>
          {fight.started_at && (
            <span className="dim" style={{ marginLeft: '1rem' }}>
              {new Date(fight.started_at).toLocaleString()}
            </span>
          )}
        </div>
        <div><strong>{fmtIsk(fight.isk_destroyed_total)}</strong> ISK destroyed total</div>
      </div>
      <h2 style={{ margin: 0 }}>Sides</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {fight.sides.map((s) => (
          <div key={s.side_idx} className="panel">
            <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>
              {s.side_kind ?? `Side ${s.side_idx + 1}`}
            </div>
            <div className="dim">{s.pilot_count} pilots · {fmtIsk(s.isk_lost)} ISK lost</div>
          </div>
        ))}
      </div>
    </div>
  )
}
