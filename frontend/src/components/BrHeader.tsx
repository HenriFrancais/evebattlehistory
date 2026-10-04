// Battle report header strip: title, where and when, then the outcome at a glance.
// Always visible above the tabs.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { BrDetail, CompositionResponse } from '../api'
import { api } from '../api'
import { fmtDateTime, fmtIsk } from '../format'
import { ArrowLeftIcon, ExternalIcon, PencilIcon } from './Icons'

interface TitleProps {
  brId: string
  title: string
  onUpdated: (newTitle: string) => void
}

function EditableTitle({ brId, title, onUpdated }: TitleProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(title)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      <div className="br-title-edit">
        <input
          data-testid="title-input"
          type="text"
          aria-label="Battle report title"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false) }}
          autoFocus
        />
        <button data-testid="save-title-btn" className="btn btn-primary" disabled={saving} onClick={() => { void save() }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
        {error && <span className="error-text" role="alert">{error}</span>}
      </div>
    )
  }
  return (
    <div className="br-title-row">
      <h1>{title}</h1>
      <button
        data-testid="edit-title-btn"
        className="btn-icon"
        aria-label="Edit title"
        title="Edit title"
        onClick={() => { setDraft(title); setError(null); setEditing(true) }}
      >
        <PencilIcon />
      </button>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="br-fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

interface Props {
  br: BrDetail
  title: string
  canEdit: boolean
  onTitleUpdated: (t: string) => void
  /** Pilot counts per side come from the composition once it has loaded. */
  composition: CompositionResponse | null
}

export function BrHeader({ br, title, canEdit, onTitleUpdated, composition }: Props) {
  const side = (kind: string) => composition?.sides.find((s) => s.side_kind === kind)?.pilot_count
  const friendly = side('friendly')
  const hostile = side('hostile')
  // Only real web links become an href (never javascript:/data:).
  const sourceUrl = br.source_url && /^https?:\/\//i.test(br.source_url) ? br.source_url : null
  return (
    <header className="br-header" data-testid="summary-section">
      <div className="br-header-top">
        <div className="br-header-id">
          <Link to="/" className="br-back"><ArrowLeftIcon /> Overview</Link>
          {canEdit
            ? <EditableTitle brId={br.br_id} title={title} onUpdated={onTitleUpdated} />
            : <div className="br-title-row"><h1>{title}</h1></div>}
          <p className="br-where">
            <span>{br.systems.length ? br.systems.join(', ') : 'Unknown system'}</span>
            <span data-testid="battle-time">{fmtDateTime(br.battle_at ?? br.created_at)} UTC</span>
          </p>
        </div>
        <div className="br-header-links">
          {sourceUrl && (
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
              {br.source} <ExternalIcon />
            </a>
          )}
          {/* FC/HC-only: the backend sends discord_thread_url solely to elevated viewers. */}
          {br.discord_thread_url && (
            <a href={br.discord_thread_url} target="_blank" rel="noopener noreferrer">
              Discord thread <ExternalIcon />
            </a>
          )}
          <Link to="/logs" className="btn" data-testid="logs-btn">Upload logs</Link>
        </div>
      </div>
      <dl className="br-facts">
        {br.result && (
          <Fact label="Result"><span className={`badge badge-${br.result}`}>{br.result}</span></Fact>
        )}
        {br.isk_efficiency != null && (
          <Fact label="ISK efficiency">{(br.isk_efficiency * 100).toFixed(1)}%</Fact>
        )}
        <Fact label="ISK destroyed"><span className="br-good">{fmtIsk(br.our_isk_destroyed)}</span></Fact>
        <Fact label="ISK lost"><span className="br-bad">{fmtIsk(br.our_isk_lost)}</span></Fact>
        {friendly != null && hostile != null && (
          <Fact label="Pilots"><span data-testid="pilot-counts">{friendly} v {hostile}</span></Fact>
        )}
        <Fact label="Engagements">{br.fight_count}</Fact>
      </dl>
    </header>
  )
}
