import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { MyLogFile } from '../api'
import { api } from '../api'
import { BulkUploader } from '../components/BulkUploader'
import { fmtDateTime } from '../format'

function StatusChip({ status }: { status: string }) {
  if (status === 'parsed') return <span className="chip chip-parsed">parsed</span>
  if (status === 'duplicate') return <span className="chip chip-duplicate">duplicate</span>
  if (status === 'unresolved') return <span className="chip chip-unresolved">unresolved</span>
  if (status === 'error') return <span className="chip chip-error">error</span>
  return <span className="chip chip-duplicate">{status}</span>
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  try {
    return `${fmtDateTime(s)} UTC`
  } catch {
    return s
  }
}

export function LogsPage() {
  const [logs, setLogs] = useState<MyLogFile[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadLogs = useCallback(() => {
    let cancelled = false
    api.myLogs().then(
      (data) => { if (!cancelled) setLogs(data) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [])

  useEffect(() => { return loadLogs() }, [loadLogs])

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="dim" style={{ fontSize: '0.85rem' }}>← Overview</Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>Logs</h1>
        </div>
      </div>

      <BulkUploader onUploaded={loadLogs} />

      {error && <p className="error-text">{error}</p>}

      <div className="panel">
        {logs === null ? (
          <p className="dim">Loading…</p>
        ) : logs.length === 0 ? (
          <p className="dim">No logs uploaded yet.</p>
        ) : (
          <table className="logs-table" data-testid="logs-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Character</th>
                <th>Listener</th>
                <th>Status</th>
                <th>Events</th>
                <th>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.file_id}>
                  <td>{log.filename}</td>
                  <td>{log.character_name ?? '—'}</td>
                  <td>{log.listener_name ?? '—'}</td>
                  <td><StatusChip status={log.parse_status} /></td>
                  <td>{log.event_count}</td>
                  <td>{fmtDate(log.uploaded_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
