import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { BrDetail, BrStatus } from '../api'
import { api } from '../api'
import { FightList } from '../components/FightList'
import { IngestProgress } from '../components/IngestProgress'
import { fmtIsk } from '../format'

export function BrDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [br, setBr] = useState<BrDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!id) return
    let cancelled = false
    api.getBr(id).then(
      (d) => { if (!cancelled) setBr(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id])

  useEffect(() => { return load() }, [load])

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!br) return <div className="page"><p className="dim">Loading…</p></div>

  const brStatus: BrStatus = {
    br_id: br.br_id,
    status: br.status,
    progress_pct: br.progress_pct,
    error_text: null,
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="dim" style={{ fontSize: '0.85rem' }}>← Timeline</Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>{br.title ?? `BR ${br.br_id}`}</h1>
        </div>
      </div>
      <div className="panel">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
          {br.result && (
            <div>
              <div className="stat-label">Result</div>
              <span className={`badge badge-${br.result}`}>{br.result}</span>
            </div>
          )}
          {br.isk_efficiency != null && (
            <div>
              <div className="stat-label">ISK Efficiency</div>
              <div className="stat-value">{(br.isk_efficiency * 100).toFixed(1)}%</div>
            </div>
          )}
          <div>
            <div className="stat-label">ISK Killed</div>
            <div className="stat-value">{fmtIsk(br.our_isk_destroyed)}</div>
          </div>
          <div>
            <div className="stat-label">ISK Lost</div>
            <div className="stat-value">{fmtIsk(br.our_isk_lost)}</div>
          </div>
          <div>
            <div className="stat-label">Engagements</div>
            <div className="stat-value">{br.fight_count}</div>
          </div>
          <div>
            <div className="stat-label">Source</div>
            <div>
              <span className="badge badge-source">{br.source}</span>
              {br.source_url && (
                <a href={br.source_url} target="_top" rel="noopener noreferrer" style={{ marginLeft: '0.5rem' }}>View source ↗</a>
              )}
            </div>
          </div>
        </div>
      </div>
      {br.status !== 'ready' && (
        <IngestProgress brId={br.br_id} initialStatus={brStatus} onReady={load} />
      )}
      <h2 style={{ margin: 0 }}>Engagements</h2>
      <FightList fights={br.fights} brId={br.br_id} />
    </div>
  )
}
