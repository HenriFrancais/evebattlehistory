import { useState } from 'react'
import { api } from '../api'

const ALLOWED_HOSTS = ['zkillboard.com', 'br.evetools.org']

interface Props {
  onCreated: (brId: string) => void
}

export function CreateBrForm({ onCreated }: Props) {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function validateUrl(value: string): string | null {
    if (!value.trim()) return 'URL is required'
    try {
      const parsed = new URL(value.trim())
      const host = parsed.hostname.replace(/^www\./, '')
      if (!ALLOWED_HOSTS.includes(host)) {
        return `URL must be from zkillboard.com or br.evetools.org (got: ${host})`
      }
    } catch {
      return 'Invalid URL'
    }
    return null
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const err = validateUrl(url)
    setUrlError(err)
    if (err) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await api.createBr(url.trim(), title.trim() || undefined)
      onCreated(result.br_id)
    } catch (ex) {
      setSubmitError(ex instanceof Error ? ex.message : String(ex))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="panel" style={{ maxWidth: '36rem' }}>
      <h2 style={{ marginTop: 0 }}>New Battle Report</h2>
      <div className="form-group">
        <label htmlFor="br-url">zKillboard or Aurora URL *</label>
        <input
          id="br-url"
          type="url"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setUrlError(null) }}
          placeholder="https://zkillboard.com/related/..."
          required
        />
        {urlError && <div className="error-text" role="alert">{urlError}</div>}
      </div>
      <div className="form-group" style={{ marginTop: '0.75rem' }}>
        <label htmlFor="br-title">Title (optional)</label>
        <input
          id="br-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Home defence 2026-06-10"
        />
      </div>
      {submitError && <div className="error-text" role="alert" style={{ marginTop: '0.5rem' }}>{submitError}</div>}
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
