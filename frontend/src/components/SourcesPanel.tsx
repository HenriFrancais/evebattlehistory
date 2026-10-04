// Sources of a battle report (zKillboard / br.evetools links and time windows):
// list, add and delete. FC / High Command only — shown on the Manage tab.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { BrSourceIn, BrSourceOut, BrStatus } from '../api'
import { api } from '../api'

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
// Add Source mini-form
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
// Sources panel
// ---------------------------------------------------------------------------

interface SourcesPanelProps {
  brId: string
  onRefreshTriggered: (status: BrStatus) => void
}

export function SourcesPanel({ brId, onRefreshTriggered }: SourcesPanelProps) {
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
    <div data-testid="sources-panel">
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
    </div>
  )
}
