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
  const [deleting, setDeleting] = useState<string | null>(null)

  const loadLogs = useCallback(() => {
    let cancelled = false
    api.myLogs().then(
      (data) => { if (!cancelled) setLogs(data) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [])

  useEffect(() => { return loadLogs() }, [loadLogs])

  async function handleDelete(log: MyLogFile) {
    const ok = window.confirm(
      `Delete ${log.filename ?? 'this log'}? Its events are removed from every battle report.`,
    )
    if (!ok) return
    setDeleting(log.file_id)
    setError(null)
    try {
      await api.deleteLog(log.file_id)
      loadLogs()
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="dim" style={{ fontSize: '0.85rem' }}>← Overview</Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>Logs</h1>
        </div>
      </div>

      <p className="dim" style={{ margin: '0 0 0.5rem', fontSize: '0.9rem' }}>
        For details on your logs and where to find them, read{' '}
        <a
          href="https://tools.novacancies.space/wiki/pages/eve-logs"
          target="_blank"
          rel="noopener noreferrer"
        >
          this wiki page
        </a>.
      </p>

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
                <th aria-label="Actions" />
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
                  <td>
                    <button
                      className="btn"
                      style={{ fontSize: '0.8rem', padding: '0.1rem 0.45rem' }}
                      disabled={deleting === log.file_id}
                      onClick={() => { void handleDelete(log) }}
                      aria-label={`Delete ${log.filename ?? 'log'}`}
                    >
                      {deleting === log.file_id ? 'Deleting…' : 'Delete'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
