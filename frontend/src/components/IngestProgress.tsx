import { useEffect, useRef, useState } from 'react'
import type { BrStatus } from '../api'
import { api } from '../api'

interface Props {
  brId: string
  initialStatus: BrStatus
  onReady?: () => void
}

const TERMINAL = new Set(['ready', 'error'])

export function IngestProgress({ brId, initialStatus, onReady }: Props) {
  const [status, setStatus] = useState<BrStatus>(initialStatus)
  const onReadyRef = useRef(onReady)
  onReadyRef.current = onReady

  useEffect(() => {
    if (TERMINAL.has(initialStatus.status)) return
    let cancelled = false
    let timer: number
    const poll = async () => {
      if (cancelled) return
      try {
        const s = await api.getBrStatus(brId)
        if (cancelled) return
        setStatus(s)
        if (TERMINAL.has(s.status)) {
          if (s.status === 'ready') onReadyRef.current?.()
          return
        }
      } catch {
        // retry next tick
      }
      if (!cancelled) timer = window.setTimeout(poll, 2000)
    }
    timer = window.setTimeout(poll, 2000)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [brId, initialStatus.status])

  if (status.status === 'error') {
    return (
      <div className="ingest-progress" data-testid="ingest-progress">
        <span className="error-text">Ingest failed: {status.error_text ?? 'unknown error'}</span>
      </div>
    )
  }

  if (status.status === 'ready') return null

  return (
    <div className="ingest-progress" data-testid="ingest-progress">
      <span>Ingesting… {status.status} ({status.progress_pct}%)</span>
      <div className="progress-bar-wrap">
        <div className="progress-bar-fill" style={{ width: `${status.progress_pct}%` }} />
      </div>
    </div>
  )
}
