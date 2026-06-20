import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { type CharacterTimeline, ApiError, api } from '../api'
import { FleetGraphCore } from '../components/FleetGraph'
import { SnapshotPanel } from '../components/SnapshotPanel'
import { toFleetTimeline } from '../timeline'

export function CharacterTimelinePage() {
  const { id, charId } = useParams<{ id: string; charId: string }>()
  const [timeline, setTimeline] = useState<CharacterTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)

  useEffect(() => {
    if (!id || !charId) return
    let cancelled = false
    setForbidden(false)
    setError(null)
    setTimeline(null)
    setRange(null)
    api.characterTimeline(id, charId).then(
      (data) => { if (!cancelled) setTimeline(data) },
      (e: unknown) => {
        if (!cancelled) {
          if (e instanceof ApiError && e.status === 403) {
            setForbidden(true)
          } else {
            setError(String((e as Error)?.message ?? e))
          }
        }
      },
    )
    return () => { cancelled = true }
  }, [id, charId])

  // Stable reference: only recompute when the fetched timeline changes, so the
  // chart doesn't rebuild on every render (that froze zoom/scrub before).
  const fleetLike = useMemo(
    () => (timeline && timeline.series.length > 0 ? toFleetTimeline(timeline) : null),
    [timeline],
  )

  if (forbidden) {
    return (
      <div className="page">
        <p className="error-text" data-testid="forbidden-message">
          You can only view your own characters' logs — ask an FC for fleet-wide access.
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page">
        <p className="error-text">{error}</p>
      </div>
    )
  }

  if (!timeline) {
    return (
      <div className="page">
        <p className="dim">Loading…</p>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to={`/brs/${id}`} className="dim" style={{ fontSize: '0.85rem' }}>
            ← BR Summary
          </Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>Character Timeline</h1>
        </div>
      </div>

      {fleetLike == null ? (
        <div className="panel">
          <p className="dim">No logs for this character in this BR. Upload combat logs to see timeline data.</p>
        </div>
      ) : (
        <div className="br-detail-grid" data-testid="br-detail-grid">
          <div className="br-col-main" data-testid="br-col-main">
            <section className="panel">
              <FleetGraphCore fleet={fleetLike} selectedRange={range} onSelectRange={setRange} />
            </section>
          </div>
          <div className="br-col-side" data-testid="br-col-side">
            <section className="panel">
              <h3 style={{ margin: '0 0 0.5rem' }}>Snapshot</h3>
              {id && charId && <SnapshotPanel brId={id} charId={charId} range={range} />}
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
