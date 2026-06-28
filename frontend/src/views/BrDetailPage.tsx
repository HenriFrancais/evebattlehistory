import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { ApiError, BrDetail, BrSourceIn, BrSourceOut, BrStatus, MeResponse, UserCoverage } from '../api'
import { api } from '../api'
import { invalidateBr, loadBr, loadMe } from '../cache'
import { CoverageMatrix } from '../components/CoverageMatrix'
import { DeferredMount } from '../components/DeferredMount'
import { FleetGraph } from '../components/FleetGraph'
import { FleetsPanel } from '../components/FleetsPanel'
import { SnapshotPanel } from '../components/SnapshotPanel'
import { SidesEditor } from '../components/SidesEditor'
import { IngestProgress } from '../components/IngestProgress'
import { fmtIsk, fmtDateTime } from '../format'

function MyCoverageSection({ id }: { id: string }) {
  const [myCoverage, setMyCoverage] = useState<UserCoverage | null>(null)
  const [noParticipation, setNoParticipation] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.myBrCoverage(id).then(
      (data) => { if (!cancelled) setMyCoverage(data) },
      (e: unknown) => {
        if (!cancelled) {
          const err = e as ApiError
          if (err?.status === 404) {
            setNoParticipation(true)
          } else {
            setError(String(err?.message ?? e))
          }
        }
      },
    )
    return () => { cancelled = true }
  }, [id])

  if (noParticipation) {
    return <p className="dim">None of your characters participated in this BR.</p>
  }
  if (error) {
    return <p className="error-text">{error}</p>
  }
  if (!myCoverage) {
    return <p className="dim">Loading coverage…</p>
  }

  return (
    <div>
      <h3 style={{ margin: '0 0 0.5rem' }}>My Characters</h3>
      {myCoverage.characters.length === 0 ? (
        <p className="dim">No characters found.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '0.35rem' }}>
          {myCoverage.characters.map((char) => (
            <div key={char.character_id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Link to={`/brs/${id}/characters/${char.character_id}`} style={{ fontWeight: 500 }}>
                {char.character_name}
              </Link>
              {/* E1: flag log-only participants */}
              {char.has_logs && char.on_killmail === false && (
                <span
                  className="badge badge-log-only"
                  data-testid={`my-log-only-badge-${char.character_id}`}
                  title="Logs only — not on a killmail"
                  style={{ fontSize: '0.75rem' }}
                >
                  logs only
                </span>
              )}
              {char.covered ? (
                <span className="cov-covered">✓ covered ({char.fights_covered.length} fights)</span>
              ) : (
                <span className="cov-missing">
                  ✗ missing {char.fights_missing.length} fight{char.fights_missing.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <p style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}>
        <Link to="/logs">Upload logs →</Link>
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// E4b: Editable title
// ---------------------------------------------------------------------------

interface EditableTitleProps {
  brId: string
  title: string
  onUpdated: (newTitle: string) => void
}

function EditableTitle({ brId, title, onUpdated }: EditableTitleProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(title)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function startEdit() {
    setDraft(title)
    setError(null)
    setEditing(true)
  }

  async function save() {
    if (!draft.trim()) return
    setSaving(true)
    setError(null)
    try {
      await api.patchBrTitle(brId, draft.trim())
      onUpdated(draft.trim())
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <input
          data-testid="title-input"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{ fontSize: '1.25rem', fontWeight: 700, background: 'var(--panel-2)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '0.25rem', padding: '0.25rem 0.5rem', minWidth: '16rem' }}
          onKeyDown={(e) => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false) }}
          autoFocus
        />
        <button data-testid="save-title-btn" className="btn btn-primary" disabled={saving} onClick={() => { void save() }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
        {error && <span className="error-text">{error}</span>}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <h1 style={{ margin: '0.25rem 0 0' }}>{title}</h1>
      <button
        data-testid="edit-title-btn"
        className="btn"
        aria-label="Edit title"
        onClick={startEdit}
        style={{ fontSize: '0.85rem', padding: '0.2rem 0.5rem', marginTop: '0.25rem' }}
      >
        ✏
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// E4b: Source status badge
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  ready: 'var(--ok)',
  pending: 'var(--warn)',
  error: 'var(--bad)',
}

function SourceStatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? 'var(--text-dim)'
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '0.1rem 0.45rem',
        borderRadius: '0.25rem',
        background: color,
        color: '#000',
        fontSize: '0.75rem',
        fontWeight: 600,
      }}
    >
      {status}
    </span>
  )
}

// ---------------------------------------------------------------------------
// E4b: Add Source mini-form
// ---------------------------------------------------------------------------

interface AddSourceFormProps {
  brId: string
  onAdded: () => void
}

function AddSourceForm({ brId, onAdded }: AddSourceFormProps) {
  const [kind, setKind] = useState<'link' | 'window'>('link')
  const [url, setUrl] = useState('')
  const [systemName, setSystemName] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    let source: BrSourceIn
    if (kind === 'link') {
      if (!url.trim()) { setError('URL is required'); return }
      try {
        const host = new URL(url.trim()).hostname.replace(/^www\./, '')
        if (!['zkillboard.com', 'br.evetools.org'].includes(host)) {
          setError(`URL must be from zkillboard.com or br.evetools.org (got: ${host})`)
          return
        }
      } catch {
        setError('Invalid URL')
        return
      }
      source = { kind: 'link', url: url.trim() }
    } else {
      if (!systemName.trim()) { setError('System name required'); return }
      if (!start || !end) { setError('Start and end are required'); return }
      const startD = new Date(start + (start.length === 16 ? ':00Z' : 'Z'))
      const endD = new Date(end + (end.length === 16 ? ':00Z' : 'Z'))
      if (startD >= endD) { setError('Start must be before end'); return }
      source = {
        kind: 'window',
        system_name: systemName.trim(),
        window_start: startD.toISOString(),
        window_end: endD.toISOString(),
        ...(label.trim() ? { label: label.trim() } : {}),
      }
    }

    setSubmitting(true)
    try {
      await api.addSource(brId, source)
      onAdded()
      setUrl(''); setSystemName(''); setStart(''); setEnd(''); setLabel('')
    } catch (ex) {
      setError(ex instanceof Error ? ex.message : String(ex))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={(e) => { void handleSubmit(e) }} style={{ marginTop: '0.75rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <label htmlFor="add-source-kind" style={{ whiteSpace: 'nowrap', fontSize: '0.85rem' }}>Kind</label>
        <select
          id="add-source-kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as 'link' | 'window')}
          style={{ background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '0.25rem', padding: '0.2rem 0.4rem', fontSize: '0.85rem' }}
        >
          <option value="link">Link</option>
          <option value="window">Time window</option>
        </select>
      </div>
      {kind === 'link' ? (
        <input
          data-testid="add-source-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://zkillboard.com/related/..."
          style={{ width: '100%', marginBottom: '0.5rem' }}
        />
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.5rem' }}>
          <input
            type="text"
            value={systemName}
            onChange={(e) => setSystemName(e.target.value)}
            placeholder="System name (e.g. J125122)"
            aria-label="System name"
            style={{ width: '12rem' }}
          />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Start (UTC)</label>
            <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>End (UTC)</label>
            <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (optional)"
          />
        </div>
      )}
      {error && <div className="error-text" role="alert" style={{ marginBottom: '0.5rem' }}>{error}</div>}
      <button
        data-testid="add-source-submit"
        type="submit"
        className="btn btn-primary"
        disabled={submitting}
        style={{ fontSize: '0.85rem' }}
      >
        {submitting ? 'Adding…' : 'Add source'}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// E4b: Sources panel
// ---------------------------------------------------------------------------

interface SourcesPanelProps {
  brId: string
  onRefreshTriggered: (status: BrStatus) => void
}

function SourcesPanel({ brId, onRefreshTriggered }: SourcesPanelProps) {
  const [sources, setSources] = useState<BrSourceOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const onRefreshTriggeredRef = useRef(onRefreshTriggered)
  onRefreshTriggeredRef.current = onRefreshTriggered

  const loadSources = useCallback(() => {
    let cancelled = false
    setLoading(true)
    api.getSources(brId).then(
      (data) => { if (!cancelled) { setSources(data); setLoading(false) } },
      (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } }
    )
    return () => { cancelled = true }
  }, [brId])

  useEffect(() => { return loadSources() }, [loadSources])

  async function handleDelete(sourceId: number) {
    try {
      await api.deleteSource(brId, sourceId)
      const status = await api.refreshBr(brId)
      onRefreshTriggeredRef.current(status)
      loadSources()
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    }
  }

  async function handleAdded() {
    const status = await api.refreshBr(brId)
    onRefreshTriggeredRef.current(status)
    loadSources()
  }

  return (
    <details data-testid="sources-panel" style={{ marginBottom: '0.5rem' }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600, marginBottom: '0.5rem' }}>
        Sources
      </summary>
      {loading && <p className="dim" style={{ fontSize: '0.85rem' }}>Loading sources…</p>}
      {error && <p className="error-text">{error}</p>}
      {!loading && sources.length === 0 && (
        <p className="dim" style={{ fontSize: '0.85rem' }}>No sources.</p>
      )}
      {sources.map((src) => (
        <div
          key={src.source_id}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.75rem',
            padding: '0.5rem',
            borderBottom: '1px solid var(--border)',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            {src.kind === 'link' ? (
              <div>
                <span className="dim" style={{ fontSize: '0.75rem' }}>Link</span>{' '}
                {src.url && (
                  <a href={src.url} target="_top" rel="noopener noreferrer" style={{ fontSize: '0.85rem', wordBreak: 'break-all' }}>
                    {src.url}
                  </a>
                )}
              </div>
            ) : (
              <div style={{ fontSize: '0.85rem' }}>
                <span className="dim">Window</span> {src.system_name ?? `sys ${src.system_id}`}
                {src.label && <span> — {src.label}</span>}
                {src.window_start && <div className="dim" style={{ fontSize: '0.75rem' }}>{src.window_start} → {src.window_end}</div>}
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
              <SourceStatusBadge status={src.status} />
              <span className="dim" style={{ fontSize: '0.75rem' }}>{src.km_count} km</span>
              {src.error_text && <span className="error-text" style={{ fontSize: '0.75rem' }}>{src.error_text}</span>}
            </div>
          </div>
          <button
            data-testid={`delete-source-${src.source_id}`}
            className="btn"
            aria-label={`Delete source ${src.source_id}`}
            onClick={() => { void handleDelete(src.source_id) }}
            style={{ fontSize: '0.85rem', padding: '0.2rem 0.5rem', color: 'var(--bad)', flexShrink: 0 }}
          >
            ×
          </button>
        </div>
      ))}
      <details data-testid="add-source-details" style={{ marginTop: '0.75rem' }}>
        <summary style={{ cursor: 'pointer', fontSize: '0.85rem', color: 'var(--accent)' }}>+ Add source</summary>
        <AddSourceForm brId={brId} onAdded={() => { void handleAdded() }} />
      </details>
    </details>
  )
}

// ---------------------------------------------------------------------------
// BrDetailPage
// ---------------------------------------------------------------------------

export function BrDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [br, setBr] = useState<BrDetail | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [displayTitle, setDisplayTitle] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [me, setMe] = useState<MeResponse | null>(null)
  const [fullCoverage, setFullCoverage] = useState<UserCoverage[] | null>(null)
  // E4b: refresh state
  const [refreshStatus, setRefreshStatus] = useState<BrStatus | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [sidesVersion, setSidesVersion] = useState(0)
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  const [graphFullscreen, setGraphFullscreen] = useState(false)

  // `force` bypasses the prefetch cache and refreshes it — used after an ingest
  // or refresh completes. The initial mount load uses the cache so a row that was
  // hovered on the overview opens already-populated.
  const load = useCallback((force = false) => {
    if (!id) return
    let cancelled = false
    loadBr(id, force).then(
      (d) => {
        if (!cancelled) {
          setBr(d)
          setDisplayTitle(d.title ?? `BR ${d.br_id}`)
        }
      },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id])

  useEffect(() => { return load(false) }, [load])

  // Close the fullscreen graph overlay on Escape.
  useEffect(() => {
    if (!graphFullscreen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setGraphFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [graphFullscreen])

  useEffect(() => {
    let cancelled = false
    loadMe().then(
      (d) => { if (!cancelled) setMe(d) },
      () => { /* ignore errors; coverage just won't show */ },
    )
    return () => { cancelled = true }
  }, [])

  // Fires as soon as id + me are known — no longer waits on getBr, so the
  // FC/HC coverage request runs in parallel with the BR detail load instead of
  // after it.
  useEffect(() => {
    if (!id || !me?.can_create_br) return
    let cancelled = false
    api.brCoverage(id).then(
      (data) => { if (!cancelled) setFullCoverage(data) },
      () => { /* non-critical; ignore */ },
    )
    return () => { cancelled = true }
  }, [id, me])

  async function handleRefresh() {
    if (!id) return
    setRefreshing(true)
    try {
      const status = await api.refreshBr(id)
      setRefreshStatus(status)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setRefreshing(false)
    }
  }

  function handleRefreshTriggered(status: BrStatus) {
    setRefreshStatus(status)
  }

  async function handleDelete() {
    if (!id) return
    const label = displayTitle || `BR ${id}`
    if (!window.confirm(`Delete "${label}"? This permanently removes the battle report and cannot be undone.`)) {
      return
    }
    setDeleting(true)
    try {
      await api.deleteBr(id)
      invalidateBr(id)
      navigate('/')
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
      setDeleting(false)
    }
  }

  function handleRefreshReady() {
    setRefreshStatus(null)
    load(true)
    // Force the data panels (composition, fleet graph, coverage) to re-fetch so newly
    // ingested sources show without a manual page reload.
    setSidesVersion((v) => v + 1)
  }

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!br) return <div className="page"><p className="dim">Loading…</p></div>

  const brStatus: BrStatus = {
    br_id: br.br_id,
    status: br.status,
    progress_pct: br.progress_pct,
    error_text: null,
  }

  const canCreate = me?.can_create_br ?? false

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="dim" style={{ fontSize: '0.85rem' }}>← Overview</Link>
          {canCreate ? (
            <EditableTitle
              brId={br.br_id}
              title={displayTitle}
              onUpdated={(t) => { setDisplayTitle(t); invalidateBr(br.br_id) }}
            />
          ) : (
            <h1 style={{ margin: '0.25rem 0 0' }}>{displayTitle}</h1>
          )}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <Link to="/logs" className="btn" data-testid="logs-btn" style={{ fontSize: '0.85rem' }}>
            Logs
          </Link>
          {canCreate && (
            <>
              <button
                data-testid="refresh-btn"
                className="btn"
                disabled={refreshing}
                onClick={() => { void handleRefresh() }}
                style={{ fontSize: '0.85rem' }}
              >
                {refreshing ? 'Refreshing…' : '↻ Refresh'}
              </button>
              <button
                data-testid="delete-br-btn"
                className="btn"
                disabled={deleting}
                onClick={() => { void handleDelete() }}
                title="Delete this battle report (FC / High Command)"
                style={{ fontSize: '0.85rem', color: 'var(--bad)', borderColor: 'var(--bad)' }}
              >
                {deleting ? 'Deleting…' : '🗑 Delete'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="panel" data-testid="summary-section">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
          <div>
            <div className="stat-label">Battle (UTC)</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {fmtDateTime(br.battle_at ?? br.created_at)}
            </div>
          </div>
          <div>
            <div className="stat-label">System</div>
            <div className="stat-value" style={{ fontSize: '1rem' }}>
              {br.systems.length ? br.systems.join(', ') : '—'}
            </div>
          </div>
          <div>
            <div className="stat-label">ISK Destroyed</div>
            <div className="stat-value">{fmtIsk(br.our_isk_destroyed)}</div>
          </div>
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
                <a href={br.source_url} target="_blank" rel="noopener noreferrer" style={{ marginLeft: '0.5rem' }}>View source ↗</a>
              )}
            </div>
          </div>
        </div>
      </div>

      {br.status !== 'ready' && (
        <IngestProgress brId={br.br_id} initialStatus={brStatus} onReady={() => load(true)} />
      )}

      {refreshStatus && (
        <IngestProgress
          brId={br.br_id}
          initialStatus={refreshStatus}
          onReady={handleRefreshReady}
        />
      )}

      {/* E4b: Sources panel (can_create_br only) */}
      {canCreate && id && (
        <div className="panel" style={{ padding: '0.75rem' }}>
          <SourcesPanel brId={id} onRefreshTriggered={handleRefreshTriggered} />
        </div>
      )}

      <section className="panel" data-testid="sides-section">
        <h2 style={{ margin: '0 0 0.75rem' }}>Sides</h2>
        {id && <SidesEditor brId={id} onChange={() => setSidesVersion((v) => v + 1)} />}
      </section>

      {/* Fleets spans the full page width; the snapshot/graph grid begins below it. */}
      <section className="panel" data-testid="fleets-section">
        {id && <FleetsPanel brId={id} reloadKey={sidesVersion} />}
      </section>

      <div className="br-detail-grid" data-testid="br-detail-grid">
        <div className="br-col-main" data-testid="br-col-main">
          <section data-testid="fleet-graph-section" className="panel">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '0 0 0.75rem' }}>
              <h2 style={{ margin: 0 }}>Fleet Graph</h2>
              <button
                type="button"
                className="btn-mini"
                data-testid="expand-graph-btn"
                aria-label="Expand graph to fullscreen"
                title="Expand to fullscreen"
                onClick={() => setGraphFullscreen(true)}
              >
                ⤢ Expand
              </button>
            </div>
            {id && !graphFullscreen && (
              <FleetGraph brId={id} reloadKey={sidesVersion} selectedRange={range} onSelectRange={setRange} />
            )}
            {graphFullscreen && <p className="dim" style={{ fontSize: '0.85rem' }}>Graph open in fullscreen…</p>}
          </section>
        </div>
        <div className="br-col-side" data-testid="br-col-side">
          <section className="panel">
            <h3 style={{ margin: '0 0 0.5rem' }}>Snapshot</h3>
            {id && <SnapshotPanel brId={id} range={range} onClearRange={() => setRange(null)} />}
          </section>
        </div>
      </div>

      <section data-testid="log-coverage-section" className="panel">
        <h2 style={{ margin: '0 0 0.75rem' }}>Log Coverage</h2>
        {id && <MyCoverageSection id={id} />}
        {me?.can_create_br && fullCoverage && (
          <div style={{ marginTop: '1rem' }}>
            <h3 style={{ margin: '0 0 0.5rem' }}>All Members</h3>
            {/* Heavy below-the-fold table — defer its render off the initial paint. */}
            <DeferredMount minHeight={120}>
              <CoverageMatrix coverage={fullCoverage} brId={id} />
            </DeferredMount>
          </div>
        )}
      </section>

      {graphFullscreen && id && (
        <div
          className="graph-overlay"
          data-testid="graph-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Fleet Graph fullscreen"
        >
          <div className="graph-overlay-head">
            <h2 style={{ margin: 0 }}>Fleet Graph</h2>
            <button
              type="button"
              className="btn"
              data-testid="close-graph-btn"
              aria-label="Close fullscreen graph"
              onClick={() => setGraphFullscreen(false)}
              style={{ fontSize: '0.85rem' }}
            >
              ✕ Close
            </button>
          </div>
          <div className="graph-overlay-body">
            <section className="panel graph-overlay-main">
              <FleetGraph brId={id} reloadKey={sidesVersion} selectedRange={range} onSelectRange={setRange} height={360} />
            </section>
            <section className="panel graph-overlay-side">
              <h3 style={{ margin: '0 0 0.5rem' }}>Snapshot</h3>
              <SnapshotPanel brId={id} range={range} onClearRange={() => setRange(null)} />
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
