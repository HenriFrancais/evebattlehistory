// Fleet-broadcast analytics panel: median summary, time-distribution histograms,
// per-pilot target-switch scoring, a flagged-deaths table, and (FC/HC only) an
// upload / replace / delete control for the BR's broadcast log.

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api'
import type { BroadcastFileInfo, BroadcastMetrics as Metrics } from '../api'

function fmtS(v: number | null): string {
  return v === null || v === undefined ? '—' : `${v.toFixed(1)}s`
}
function fmtPct(v: number | null): string {
  return v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 90 }}>
      <span style={{ fontSize: '1.25rem', fontWeight: 600 }}>{value}</span>
      <span className="dim" style={{ fontSize: '0.72rem' }}>
        {label}
      </span>
    </div>
  )
}

interface Props {
  brId: string
  canManage: boolean
  /** Bump to force a re-fetch from the parent (e.g. side edits). */
  reloadKey?: number
  /** Called after a successful upload/delete so the parent can refresh the timeline. */
  onChange?: () => void
  /** Lifts the loaded metrics to the parent (e.g. to derive flagged deaths for the graph). */
  onLoaded?: (m: Metrics) => void
}

export function BroadcastMetrics({ brId, canManage, reloadKey, onChange, onLoaded }: Props) {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [file, setFile] = useState<BroadcastFileInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [version, setVersion] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([api.broadcastMetrics(brId), api.broadcastFile(brId)]).then(
      ([m, f]) => {
        if (!cancelled) {
          setMetrics(m)
          setFile(f)
          setLoading(false)
          onLoaded?.(m)
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(String((e as Error)?.message ?? e))
          setLoading(false)
        }
      },
    )
    return () => {
      cancelled = true
    }
  }, [brId, reloadKey, version])

  const doUpload = useCallback(
    async (f: File) => {
      setBusy(true)
      setError(null)
      try {
        await api.uploadBroadcast(brId, f)
        setVersion((v) => v + 1)
        onChange?.()
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [brId, onChange],
  )

  const doDelete = useCallback(async () => {
    if (!file) return
    if (!window.confirm('Delete the broadcast log for this battle report?')) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteBroadcast(brId, file.file_id)
      setVersion((v) => v + 1)
      onChange?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [brId, file, onChange])

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) void doUpload(f)
    e.target.value = '' // allow re-selecting the same filename
  }

  const manageControl = canManage ? (
    <div
      data-testid="broadcast-manage"
      style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt,text/plain"
        onChange={onPick}
        style={{ display: 'none' }}
        data-testid="broadcast-file-input"
      />
      {file ? (
        <>
          <span className="dim" style={{ fontSize: '0.8rem' }}>
            {file.original_filename ?? 'broadcast.txt'} · {file.broadcast_count} broadcasts ·{' '}
            {file.uploaded_by_user}
          </span>
          <button
            type="button"
            className="btn btn-mini"
            disabled={busy}
            data-testid="broadcast-replace-btn"
            onClick={() => fileInputRef.current?.click()}
          >
            Replace
          </button>
          <button
            type="button"
            className="btn btn-mini"
            disabled={busy}
            data-testid="broadcast-delete-btn"
            onClick={() => void doDelete()}
          >
            Delete
          </button>
        </>
      ) : (
        <button
          type="button"
          className="btn btn-primary btn-mini"
          disabled={busy}
          data-testid="broadcast-upload-btn"
          onClick={() => fileInputRef.current?.click()}
        >
          Upload broadcast log
        </button>
      )}
    </div>
  ) : null

  if (loading) return <p className="dim">Loading broadcast data…</p>
  if (error && !metrics)
    return (
      <div>
        <p className="error-text" data-testid="broadcast-error">
          {error}
        </p>
        {manageControl}
      </div>
    )

  if (!metrics || !metrics.has_broadcasts) {
    return (
      <div data-testid="broadcast-empty">
        <p className="dim" style={{ fontSize: '0.85rem' }}>
          No fleet-broadcast log has been linked to this battle report.
          {canManage ? '' : ' An FC or High Command member can upload one.'}
        </p>
        {manageControl}
      </div>
    )
  }

  const s = metrics.summary

  return (
    <div data-testid="broadcast-metrics">
      {error && (
        <p className="error-text" style={{ fontSize: '0.8rem' }}>
          {error}
        </p>
      )}
      <div
        style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}
        data-testid="broadcast-summary"
      >
        <Stat label="Median time-to-fire" value={fmtS(s.median_time_to_fire_s)} />
        <Stat label="Target compliance" value={fmtPct(s.compliance_rate)} />
        <Stat label="Median logi response" value={fmtS(s.median_logi_response_s)} />
        <Stat label="Median reaction" value={fmtS(s.median_reaction_s)} />
        <Stat label="False broadcasts" value={fmtPct(s.false_broadcast_rate)} />
        <Stat label="Flagged deaths" value={`${s.deaths_flagged}/${s.deaths_total}`} />
      </div>

      <p className="dim" style={{ fontSize: '0.75rem', margin: '0 0 0.5rem' }}>
        Fleet-wide aggregate. Per-character breakdowns are in the Performance section.
      </p>

      {manageControl}
    </div>
  )
}
