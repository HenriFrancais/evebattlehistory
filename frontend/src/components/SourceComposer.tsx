import { useState } from 'react'
import type { BrSourceIn } from '../api'
import { api } from '../api'

// Only zKillboard is reliably resolvable (br.evetools BR API expires reports).
const ALLOWED_HOSTS = ['zkillboard.com']

type SourceKind = 'link' | 'window'

interface SourceRow {
  id: number
  kind: SourceKind
  // link fields
  url: string
  // window fields
  system_name: string
  window_start: string   // datetime-local string "YYYY-MM-DDTHH:mm"
  window_end: string
  label: string
}

function makeRow(id: number): SourceRow {
  return { id, kind: 'link', url: '', system_name: '', window_start: '', window_end: '', label: '' }
}

/** Convert a datetime-local string (UTC semantics) to ISO 8601 UTC string. */
function toUtcIso(datetimeLocal: string): string {
  // datetimeLocal looks like "2026-06-10T18:00" (16 chars) or "2026-06-10T18:00:00" (19 chars)
  const suffix = datetimeLocal.length === 16 ? ':00Z' : 'Z'
  return new Date(datetimeLocal + suffix).toISOString()
}

function validateRow(row: SourceRow): string | null {
  if (row.kind === 'link') {
    if (!row.url.trim()) return 'URL is required'
    try {
      const parsed = new URL(row.url.trim())
      const host = parsed.hostname.replace(/^www\./, '')
      if (!ALLOWED_HOSTS.includes(host)) {
        return `URL must be a zkillboard.com /related/ link (got: ${host})`
      }
    } catch {
      return 'Invalid URL'
    }
  } else {
    if (!row.system_name.trim()) return 'System name is required'
    if (!row.window_start) return 'Window start is required'
    if (!row.window_end) return 'Window end is required'
    const start = new Date(row.window_start + (row.window_start.length === 16 ? ':00Z' : 'Z'))
    const end = new Date(row.window_end + (row.window_end.length === 16 ? ':00Z' : 'Z'))
    if (start >= end) return 'Window start must be before window end'
  }
  return null
}

interface Props {
  onCreated: (brId: string) => void
}

let _nextId = 1

export function SourceComposer({ onCreated }: Props) {
  const [title, setTitle] = useState('')
  const [rows, setRows] = useState<SourceRow[]>(() => [makeRow(_nextId++)])
  const [errors, setErrors] = useState<(string | null)[]>([null])
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function addRow() {
    setRows((prev) => [...prev, makeRow(_nextId++)])
    setErrors((prev) => [...prev, null])
  }

  function removeRow(idx: number) {
    if (rows.length <= 1) return
    setRows((prev) => prev.filter((_, i) => i !== idx))
    setErrors((prev) => prev.filter((_, i) => i !== idx))
  }

  function updateRow(idx: number, patch: Partial<SourceRow>) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
    setErrors((prev) => prev.map((e, i) => (i === idx ? null : e)))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // Validate all rows
    const newErrors = rows.map(validateRow)
    setErrors(newErrors)
    if (newErrors.some((e) => e !== null)) return

    const sources: BrSourceIn[] = rows.map((row): BrSourceIn => {
      if (row.kind === 'link') {
        return { kind: 'link', url: row.url.trim() }
      } else {
        return {
          kind: 'window',
          system_name: row.system_name.trim(),
          window_start: toUtcIso(row.window_start),
          window_end: toUtcIso(row.window_end),
          ...(row.label.trim() ? { label: row.label.trim() } : {}),
        }
      }
    })

    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await api.createBr({
        sources,
        ...(title.trim() ? { title: title.trim() } : {}),
      })
      onCreated(result.br_id)
    } catch (ex) {
      setSubmitError(ex instanceof Error ? ex.message : String(ex))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="panel" style={{ maxWidth: '42rem' }}>
      <h2 style={{ marginTop: 0 }}>New Battle Report</h2>

      <div className="form-group" style={{ marginBottom: '0.75rem' }}>
        <label htmlFor="composer-title">Title (optional)</label>
        <input
          id="composer-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Home defence 2026-06-10"
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {rows.map((row, idx) => (
          <SourceRowEditor
            key={row.id}
            row={row}
            error={errors[idx]}
            canRemove={rows.length > 1}
            onChange={(patch) => updateRow(idx, patch)}
            onRemove={() => removeRow(idx)}
          />
        ))}
      </div>

      <button
        type="button"
        className="btn"
        style={{ marginTop: '0.75rem' }}
        onClick={addRow}
      >
        + Add source
      </button>

      {submitError && (
        <div className="error-text" role="alert" style={{ marginTop: '0.5rem' }}>
          {submitError}
        </div>
      )}

      <button
        type="submit"
        className="btn btn-primary"
        style={{ marginTop: '1rem' }}
        disabled={submitting}
      >
        {submitting ? 'Submitting…' : 'Create BR'}
      </button>
    </form>
  )
}

interface RowEditorProps {
  row: SourceRow
  error: string | null
  canRemove: boolean
  onChange: (patch: Partial<SourceRow>) => void
  onRemove: () => void
}

function SourceRowEditor({ row, error, canRemove, onChange, onRemove }: RowEditorProps) {
  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: '0.375rem',
        padding: '0.75rem',
        background: 'var(--panel-2)',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <label htmlFor={`type-${row.id}`} style={{ whiteSpace: 'nowrap' }}>
          Source type
        </label>
        <select
          id={`type-${row.id}`}
          aria-label="Source type"
          value={row.kind}
          onChange={(e) => onChange({ kind: e.target.value as SourceKind, url: '', system_name: '', window_start: '', window_end: '', label: '' })}
          style={{ background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '0.25rem', padding: '0.25rem 0.5rem' }}
        >
          <option value="link">Link (zKB / Aurora)</option>
          <option value="window">Time window</option>
        </select>
        <button
          type="button"
          className="btn"
          aria-label="Remove source"
          disabled={!canRemove}
          onClick={onRemove}
          style={{ marginLeft: 'auto', minWidth: '2rem', padding: '0.2rem 0.5rem' }}
        >
          ×
        </button>
      </div>

      {row.kind === 'link' ? (
        <div className="form-group">
          <input
            type="url"
            value={row.url}
            onChange={(e) => onChange({ url: e.target.value })}
            placeholder="https://zkillboard.com/related/..."
          />
        </div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
          <div className="form-group">
            <input
              type="text"
              value={row.system_name}
              onChange={(e) => onChange({ system_name: e.target.value })}
              placeholder="System name (e.g. J125122)"
              aria-label="System name"
              style={{ width: '12rem' }}
            />
          </div>
          <div className="form-group" style={{ display: 'flex', flexDirection: 'column' }}>
            <label htmlFor={`start-${row.id}`} style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Start (UTC)</label>
            <input
              id={`start-${row.id}`}
              type="datetime-local"
              value={row.window_start}
              onChange={(e) => onChange({ window_start: e.target.value })}
            />
          </div>
          <div className="form-group" style={{ display: 'flex', flexDirection: 'column' }}>
            <label htmlFor={`end-${row.id}`} style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>End (UTC)</label>
            <input
              id={`end-${row.id}`}
              type="datetime-local"
              value={row.window_end}
              onChange={(e) => onChange({ window_end: e.target.value })}
            />
          </div>
          <div className="form-group">
            <input
              type="text"
              value={row.label}
              onChange={(e) => onChange({ label: e.target.value })}
              placeholder="Label (optional)"
            />
          </div>
        </div>
      )}

      {error && (
        <div className="error-text" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}
