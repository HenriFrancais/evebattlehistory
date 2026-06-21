import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { BrDetail, CharacterReconcileRow, FightEwar, FightOut, FightReconcile } from '../api'
import { api } from '../api'
import { fmtDateTime, fmtIsk } from '../format'

function DpsSparkline({ series }: { series: import('../api').DpsPoint[] }) {
  if (series.length === 0) {
    return <p className="dim" data-testid="dps-sparkline">No DPS data</p>
  }
  const W = 400
  const H = 60
  const minTs = series[0].bucket_ts_epoch
  const maxTs = series[series.length - 1].bucket_ts_epoch
  const maxVal = Math.max(...series.map((p) => p.sum_damage_out), 1)
  const tsRange = maxTs - minTs || 1
  const pts = series.map((p) => {
    const x = ((p.bucket_ts_epoch - minTs) / tsRange) * W
    const y = H - (p.sum_damage_out / maxVal) * H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return (
    <div data-testid="dps-sparkline" style={{ marginBottom: '0.5rem' }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        style={{ display: 'block' }}
      >
        <polyline
          points={pts.join(' ')}
          fill="none"
          stroke="#4ade80"
          strokeWidth="1.5"
        />
      </svg>
    </div>
  )
}

function ReconcilePanel({ brId, fightId }: { brId: string; fightId: string }) {
  const [data, setData] = useState<FightReconcile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.fightReconcile(brId, fightId).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, fightId])

  if (error) return <p className="error-text">{error}</p>
  if (!data) return <p className="dim">Loading reconcile…</p>

  return (
    <div className="panel" data-testid="reconcile-panel">
      <h3 style={{ margin: '0 0 0.75rem' }}>Damage Reconcile</h3>
      <DpsSparkline series={data.dps_series} />
      {data.rows.length === 0 ? (
        <p className="dim">No reconcile data.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Character</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Log Out</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Log In</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>KM Attributed</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Delta</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row: CharacterReconcileRow) => (
              <tr
                key={row.character_id}
                className={row.delta > 0 ? 'delta-positive' : row.delta < 0 ? 'delta-negative' : ''}
                style={row.delta > 0 ? { color: '#4ade80' } : row.delta < 0 ? { color: '#f87171' } : undefined}
              >
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.character_name ?? String(row.character_id)}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.log_damage_out.toLocaleString()}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.log_damage_in.toLocaleString()}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.km_damage_attributed.toLocaleString()}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.delta.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function EwarPanel({ brId, fightId }: { brId: string; fightId: string }) {
  const [data, setData] = useState<FightEwar | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.fightEwar(brId, fightId).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, fightId])

  if (error) return <p className="error-text">{error}</p>
  if (!data) return <p className="dim">Loading EWAR data…</p>

  return (
    <div className="panel" data-testid="ewar-panel">
      <h3 style={{ margin: '0 0 0.75rem' }}>EWAR / Warfare</h3>

      <h4 style={{ margin: '0 0 0.4rem' }}>EWAR / Tackle</h4>
      {data.ewar.length === 0 ? (
        <p className="dim">No EWAR data</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', marginBottom: '1rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Character</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Effect</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Direction</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Count</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Time Range</th>
            </tr>
          </thead>
          <tbody>
            {data.ewar.map((row, i) => (
              <tr key={i}>
                <td style={{ padding: '0.25rem 0.5rem' }}>
                  {row.source_name != null
                    ? `${row.source_name} → ${row.target_name ?? '?'}`
                    : row.character_id}
                </td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.effect_type}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.direction}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.event_count}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.first_ts} – {row.last_ts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 style={{ margin: '0 0 0.4rem' }}>Cap Warfare</h4>
      {data.cap.length === 0 ? (
        <p className="dim">No cap warfare data</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', marginBottom: '1rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Character</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Effect</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Direction</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Amount</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Count</th>
            </tr>
          </thead>
          <tbody>
            {data.cap.map((row, i) => (
              <tr key={i}>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.character_id}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.effect_type}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.direction}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.sum_amount.toLocaleString()}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.event_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 style={{ margin: '0 0 0.4rem' }}>Logi Reps</h4>
      {data.logi.length === 0 ? (
        <p className="dim">No logi data</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Character</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Effect</th>
              <th style={{ textAlign: 'left', padding: '0.25rem 0.5rem' }}>Direction</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Amount</th>
              <th style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>Count</th>
            </tr>
          </thead>
          <tbody>
            {data.logi.map((row, i) => (
              <tr key={i}>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.character_id}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.effect_type}</td>
                <td style={{ padding: '0.25rem 0.5rem' }}>{row.direction}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.sum_amount.toLocaleString()}</td>
                <td style={{ textAlign: 'right', padding: '0.25rem 0.5rem' }}>{row.event_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

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
              {fmtDateTime(fight.started_at)} UTC
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
      {id && fid && (
        <>
          <ReconcilePanel brId={id} fightId={fid} />
          <EwarPanel brId={id} fightId={fid} />
        </>
      )}
    </div>
  )
}
