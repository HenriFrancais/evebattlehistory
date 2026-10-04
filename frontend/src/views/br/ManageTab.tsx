// Manage tab (FC / High Command only): where the report's data comes from, which
// side each entity is on, who still owes a log, and the refresh / delete actions.
import { useEffect, useState } from 'react'
import type { BrStatus, UserCoverage } from '../../api'
import { api } from '../../api'
import { CoverageMatrix } from '../../components/CoverageMatrix'
import { SidesEditor } from '../../components/SidesEditor'
import { SourcesPanel } from '../../components/SourcesPanel'

interface Props {
  brId: string
  title: string
  onRefreshTriggered: (status: BrStatus) => void
  onSidesChanged: () => void
  onDeleted: () => void
  onError: (message: string) => void
}

export function ManageTab({ brId, title, onRefreshTriggered, onSidesChanged, onDeleted, onError }: Props) {
  const [coverage, setCoverage] = useState<UserCoverage[] | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.brCoverage(brId).then(
      (data) => { if (!cancelled) setCoverage(data) },
      () => { /* non-critical: the section simply stays empty */ },
    )
    return () => { cancelled = true }
  }, [brId])

  async function handleRefresh() {
    setRefreshing(true)
    try {
      onRefreshTriggered(await api.refreshBr(brId))
    } catch (e: unknown) {
      onError(String((e as Error)?.message ?? e))
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete "${title}"? This permanently removes the battle report and cannot be undone.`)) {
      return
    }
    setDeleting(true)
    try {
      await api.deleteBr(brId)
      onDeleted()
    } catch (e: unknown) {
      onError(String((e as Error)?.message ?? e))
      setDeleting(false)
    }
  }

  return (
    <div className="tab-stack" data-testid="manage-tab">
      <div className="manage-grid">
        <section className="panel">
          <h2>Sources</h2>
          <SourcesPanel brId={brId} onRefreshTriggered={onRefreshTriggered} />
        </section>
        <section className="panel" data-testid="sides-section">
          <h2>Sides</h2>
          <SidesEditor brId={brId} onChange={onSidesChanged} />
        </section>
      </div>
      <section className="panel" data-testid="log-coverage-section">
        <h2>Log coverage</h2>
        {coverage
          ? <CoverageMatrix coverage={coverage} brId={brId} />
          : <p className="dim">Loading coverage…</p>}
      </section>
      <section className="panel manage-actions">
        <div>
          <h2>Refresh from sources</h2>
          <p className="dim">Fetches the killmails again and rebuilds the report. Uploaded logs are kept.</p>
          <button data-testid="refresh-btn" className="btn" disabled={refreshing} onClick={() => { void handleRefresh() }}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        <div>
          <h2>Delete this battle report</h2>
          <p className="dim">Removes the report, its AAR and comments for everyone. This cannot be undone.</p>
          <button data-testid="delete-br-btn" className="btn btn-danger" disabled={deleting} onClick={() => { void handleDelete() }}>
            {deleting ? 'Deleting…' : 'Delete battle report'}
          </button>
        </div>
      </section>
    </div>
  )
}
