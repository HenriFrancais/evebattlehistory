import { useEffect, useState } from 'react'
import type { AarComment, AarPanel as AarPanelData, ReactionGroup, ReactionTargetType } from '../api'
import { api } from '../api'
import { fmtDateTime } from '../format'
import { Markdown } from './Markdown'

interface Props {
  brId: string
  /** True for FC / High Command — may write/edit/delete the AAR and moderate comments. */
  canManage: boolean
}

/**
 * After Action Report panel: one shared markdown report per BR (FC/HC authored),
 * plus flat comments and emoji reactions open to all authenticated users.
 */
export function AarPanel({ brId, canManage }: Props) {
  const [panel, setPanel] = useState<AarPanelData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.getAar(brId).then(
      (p) => { if (!cancelled) { setPanel(p); setLoading(false) } },
      (e) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
    )
    return () => { cancelled = true }
  }, [brId, version])

  const refresh = () => setVersion((v) => v + 1)

  // Reactions update in place (no full refetch) so nothing collapses under the user.
  const applyReactions = (
    targetType: ReactionTargetType,
    targetId: number,
    groups: ReactionGroup[],
  ) => {
    setPanel((prev) => {
      if (!prev) return prev
      if (targetType === 'aar') return { ...prev, reactions: groups }
      return {
        ...prev,
        comments: prev.comments.map((c) =>
          c.comment_id === targetId ? { ...c, reactions: groups } : c,
        ),
      }
    })
  }

  const toggleReaction = async (
    targetType: ReactionTargetType,
    targetId: number,
    emoji: string,
  ) => {
    try {
      const res = await api.toggleAarReaction(brId, targetType, targetId, emoji)
      applyReactions(res.target_type, res.target_id, res.reactions)
    } catch {
      // On failure, resync from the server.
      refresh()
    }
  }

  if (loading) return <p className="dim">Loading AAR…</p>
  if (error) return <p className="error-text" role="alert">{error}</p>
  if (!panel) return null

  const allowed = panel.viewer.allowed_reactions

  return (
    <div data-testid="aar-panel">
      <h2 style={{ margin: '0 0 0.75rem', fontSize: '1.1rem' }}>After Action Report</h2>

      {panel.aar ? (
        <AarBodyView
          brId={brId}
          canManage={canManage}
          aar={panel.aar}
          reactions={panel.reactions}
          allowed={allowed}
          onChanged={refresh}
          onToggleReaction={toggleReaction}
        />
      ) : (
        <EmptyAar brId={brId} canManage={canManage} onSaved={refresh} />
      )}

      {panel.aar && (
        <div style={{ marginTop: '1.25rem' }}>
          <h3 style={{ fontSize: '0.95rem', margin: '0 0 0.5rem' }}>
            Comments {panel.comments.length ? `(${panel.comments.length})` : ''}
          </h3>
          {panel.comments.length === 0 && (
            <p className="dim" style={{ margin: '0 0 0.5rem' }}>No comments yet.</p>
          )}
          {panel.comments.map((c) => (
            <CommentItem
              key={c.comment_id}
              brId={brId}
              comment={c}
              allowed={allowed}
              onChanged={refresh}
              onToggleReaction={toggleReaction}
            />
          ))}
          <CommentComposer brId={brId} onPosted={refresh} />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AAR body: read view + FC/HC edit/delete; empty state; editor
// ---------------------------------------------------------------------------

function EmptyAar({ brId, canManage, onSaved }: { brId: string; canManage: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return <AarEditor brId={brId} initial="" onDone={() => { setEditing(false); onSaved() }} onCancel={() => setEditing(false)} />
  }
  return (
    <div>
      <p className="dim" style={{ margin: 0 }}>
        No After Action Report has been written for this battle yet.
      </p>
      {canManage && (
        <button
          className="btn btn-primary"
          data-testid="aar-write-btn"
          style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}
          onClick={() => setEditing(true)}
        >
          ✍ Write an AAR
        </button>
      )}
    </div>
  )
}

interface BodyViewProps {
  brId: string
  canManage: boolean
  aar: NonNullable<AarPanelData['aar']>
  reactions: ReactionGroup[]
  allowed: string[]
  onChanged: () => void
  onToggleReaction: (t: ReactionTargetType, id: number, emoji: string) => void
}

function AarBodyView({ brId, canManage, aar, reactions, allowed, onChanged, onToggleReaction }: BodyViewProps) {
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (!window.confirm('Delete this AAR and all its comments? This cannot be undone.')) return
    setDeleting(true)
    try {
      await api.deleteAar(brId)
      onChanged()
    } catch (e) {
      window.alert(`Delete failed: ${String((e as Error)?.message ?? e)}`)
      setDeleting(false)
    }
  }

  if (editing) {
    return <AarEditor brId={brId} initial={aar.body} onDone={() => { setEditing(false); onChanged() }} onCancel={() => setEditing(false)} />
  }

  return (
    <div>
      <div data-testid="aar-body"><Markdown>{aar.body}</Markdown></div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <span className="dim" style={{ fontSize: '0.8rem' }}>
          Updated by {aar.updated_by_user} · {fmtDateTime(aar.updated_at)}
        </span>
        <ReactionBar
          targetType="aar"
          targetId={aar.aar_id}
          groups={reactions}
          allowed={allowed}
          onToggle={(emoji) => onToggleReaction('aar', aar.aar_id, emoji)}
        />
        {canManage && (
          <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-mini" data-testid="aar-edit-btn" onClick={() => setEditing(true)}>Edit</button>
            <button
              className="btn btn-mini"
              data-testid="aar-delete-btn"
              disabled={deleting}
              style={{ color: 'var(--bad)', borderColor: 'var(--bad)' }}
              onClick={() => { void handleDelete() }}
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </span>
        )}
      </div>
    </div>
  )
}

function AarEditor({ brId, initial, onDone, onCancel }: { brId: string; initial: string; onDone: () => void; onCancel: () => void }) {
  const [text, setText] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!text.trim()) { setError('The AAR cannot be empty.'); return }
    setSaving(true)
    setError(null)
    try {
      await api.putAar(brId, text)
      onDone()
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
      setSaving(false)
    }
  }

  return (
    <div>
      <textarea
        data-testid="aar-editor"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        placeholder="Write the After Action Report. Markdown supported: # headings, **bold**, *italic*, --- rules, lists, > quotes."
        style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.9rem', resize: 'vertical' }}
      />
      <p className="dim" style={{ fontSize: '0.75rem', margin: '0.25rem 0 0.5rem' }}>
        Markdown: <code># heading</code> · <code>**bold**</code> · <code>*italic*</code> · <code>---</code> rule · <code>- list</code> · <code>&gt; quote</code>
      </p>
      {error && <p className="error-text" role="alert" style={{ margin: '0 0 0.5rem' }}>{error}</p>}
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button className="btn btn-primary" data-testid="aar-save-btn" disabled={saving} onClick={() => { void save() }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button className="btn" disabled={saving} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Comments
// ---------------------------------------------------------------------------

interface CommentItemProps {
  brId: string
  comment: AarComment
  allowed: string[]
  onChanged: () => void
  onToggleReaction: (t: ReactionTargetType, id: number, emoji: string) => void
}

function CommentItem({ brId, comment, allowed, onChanged, onToggleReaction }: CommentItemProps) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(comment.body)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const saveEdit = async () => {
    if (!text.trim()) { setError('Comment cannot be empty.'); return }
    setBusy(true)
    setError(null)
    try {
      await api.editAarComment(brId, comment.comment_id, text)
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  const del = async () => {
    if (!window.confirm('Delete this comment?')) return
    setBusy(true)
    try {
      await api.deleteAarComment(brId, comment.comment_id)
      onChanged()
    } catch (e) {
      window.alert(`Delete failed: ${String((e as Error)?.message ?? e)}`)
      setBusy(false)
    }
  }

  return (
    <div
      data-testid="aar-comment"
      style={{ borderTop: '1px solid var(--border)', padding: '0.6rem 0' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: '0.85rem' }}>{comment.author_user}</strong>
        <span className="dim" style={{ fontSize: '0.75rem' }}>
          {fmtDateTime(comment.created_at)}{comment.updated_at ? ' · edited' : ''}
        </span>
      </div>

      {editing ? (
        <div style={{ marginTop: '0.35rem' }}>
          <textarea
            data-testid="aar-comment-editor"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.85rem', resize: 'vertical' }}
          />
          {error && <p className="error-text" role="alert" style={{ margin: '0.25rem 0' }}>{error}</p>}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
            <button className="btn btn-mini btn-primary" disabled={busy} onClick={() => { void saveEdit() }}>Save</button>
            <button className="btn btn-mini" disabled={busy} onClick={() => { setEditing(false); setText(comment.body); setError(null) }}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div style={{ marginTop: '0.2rem', fontSize: '0.9rem' }}><Markdown>{comment.body}</Markdown></div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.35rem', flexWrap: 'wrap' }}>
            <ReactionBar
              targetType="comment"
              targetId={comment.comment_id}
              groups={comment.reactions}
              allowed={allowed}
              onToggle={(emoji) => onToggleReaction('comment', comment.comment_id, emoji)}
            />
            <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem' }}>
              {comment.editable && (
                <button className="btn btn-mini" data-testid="aar-comment-edit-btn" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
              )}
              {comment.deletable && (
                <button
                  className="btn btn-mini"
                  data-testid="aar-comment-delete-btn"
                  disabled={busy}
                  style={{ color: 'var(--bad)', borderColor: 'var(--bad)' }}
                  onClick={() => { void del() }}
                >Delete</button>
              )}
            </span>
          </div>
        </>
      )}
    </div>
  )
}

function CommentComposer({ brId, onPosted }: { brId: string; onPosted: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const post = async () => {
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    try {
      await api.createAarComment(brId, text)
      setText('')
      onPosted()
    } catch (e) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border)', paddingTop: '0.6rem' }}>
      <textarea
        data-testid="aar-comment-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="Add a comment… (markdown supported)"
        style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.85rem', resize: 'vertical' }}
      />
      {error && <p className="error-text" role="alert" style={{ margin: '0.25rem 0' }}>{error}</p>}
      <button
        className="btn btn-primary"
        data-testid="aar-comment-post-btn"
        disabled={busy || !text.trim()}
        style={{ marginTop: '0.25rem', fontSize: '0.85rem' }}
        onClick={() => { void post() }}
      >
        {busy ? 'Posting…' : 'Post comment'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Reactions
// ---------------------------------------------------------------------------

function ReactionBar({
  targetType,
  targetId,
  groups,
  allowed,
  onToggle,
}: {
  targetType: ReactionTargetType
  targetId: number
  groups: ReactionGroup[]
  allowed: string[]
  onToggle: (emoji: string) => void
}) {
  const [picking, setPicking] = useState(false)
  const active = new Set(groups.filter((g) => g.count > 0).map((g) => g.emoji))

  return (
    <span
      data-testid={`aar-reactions-${targetType}-${targetId}`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', flexWrap: 'wrap', position: 'relative' }}
    >
      {groups.filter((g) => g.count > 0).map((g) => (
        <button
          key={g.emoji}
          className="btn btn-mini"
          title={g.user_names.join(', ')}
          onClick={() => onToggle(g.emoji)}
          style={{
            padding: '0.05rem 0.4rem',
            borderColor: g.reacted_by_me ? 'var(--accent)' : 'var(--border)',
            background: g.reacted_by_me ? 'color-mix(in srgb, var(--accent) 18%, transparent)' : 'transparent',
          }}
        >
          <span>{g.emoji}</span> <span style={{ fontVariantNumeric: 'tabular-nums' }}>{g.count}</span>
        </button>
      ))}
      <button
        className="btn btn-mini"
        aria-label="Add reaction"
        onClick={() => setPicking((p) => !p)}
        style={{ padding: '0.05rem 0.4rem' }}
      >
        ＋
      </button>
      {picking && (
        <span
          role="menu"
          style={{
            display: 'inline-flex',
            gap: '0.15rem',
            padding: '0.2rem',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            background: 'var(--panel)',
          }}
        >
          {allowed.map((emoji) => (
            <button
              key={emoji}
              className="btn btn-mini"
              onClick={() => { onToggle(emoji); setPicking(false) }}
              style={{
                padding: '0.05rem 0.35rem',
                borderColor: active.has(emoji) ? 'var(--accent)' : 'var(--border)',
              }}
            >
              {emoji}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}
