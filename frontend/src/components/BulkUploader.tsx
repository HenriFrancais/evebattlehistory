// Drop zone + file input; headline workflow is selecting a whole folder of logs.
// Shows per-file status chips after upload and summary counts.
// Props: onUploaded: () => void (called after successful upload to refresh table)

import { useRef, useState } from 'react'
import type { LogUploadResult } from '../api'
import { api } from '../api'

interface Props {
  onUploaded: () => void
}

function StatusChip({ result }: { result: LogUploadResult }) {
  if (result.status === 'parsed') {
    return <span className="chip chip-parsed">{result.character_name ?? result.filename}: parsed</span>
  }
  if (result.status === 'duplicate') {
    return <span className="chip chip-duplicate">{result.filename}: already uploaded</span>
  }
  if (result.status === 'unresolved') {
    return <span className="chip chip-unresolved">{result.filename}: character not matched</span>
  }
  // error
  return <span className="chip chip-error">{result.filename}: {result.message ?? 'error'}</span>
}

export function BulkUploader({ onUploaded }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [results, setResults] = useState<LogUploadResult[] | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function handleFiles(newFiles: FileList | File[]) {
    const arr = Array.from(newFiles)
    setFiles(arr)
    setResults(null)
    setError(null)
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files)
    }
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(true)
  }

  function handleDragLeave() {
    setDragOver(false)
  }

  async function handleSubmit() {
    if (files.length === 0) return
    setUploading(true)
    setError(null)
    setResults(null)
    try {
      const res = await api.uploadLogs(files)
      setResults(res)
      onUploaded()
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setUploading(false)
    }
  }

  const parsedCount = results?.filter((r) => r.status === 'parsed').length ?? 0
  const duplicateCount = results?.filter((r) => r.status === 'duplicate').length ?? 0
  const unresolvedCount = results?.filter((r) => r.status === 'unresolved').length ?? 0
  const errorCount = results?.filter((r) => r.status === 'error').length ?? 0

  const summaryParts: string[] = []
  if (parsedCount > 0) summaryParts.push(`${parsedCount} parsed`)
  if (duplicateCount > 0) summaryParts.push(`${duplicateCount} duplicate${duplicateCount !== 1 ? 's' : ''}`)
  if (unresolvedCount > 0) summaryParts.push(`${unresolvedCount} unresolved`)
  if (errorCount > 0) summaryParts.push(`${errorCount} error${errorCount !== 1 ? 's' : ''}`)

  return (
    <div data-testid="bulk-uploader" className="panel">
      <div
        className={`drop-zone${dragOver ? ' drag-over' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => inputRef.current?.click()}
      >
        <p style={{ margin: 0, color: 'var(--text-dim)' }}>Select log folder or drag &amp; drop files</p>
        {files.length > 0 && (
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.85rem' }}>
            {files.length} file{files.length !== 1 ? 's' : ''} selected
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          {...{ webkitdirectory: '' }}
          style={{ display: 'none' }}
          onChange={(e) => e.target.files && handleFiles(e.target.files)}
          aria-label="Select log files"
        />
      </div>

      <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <button
          className="btn btn-primary"
          onClick={handleSubmit}
          disabled={uploading || files.length === 0}
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {results && (
        <div style={{ marginTop: '0.75rem' }}>
          {summaryParts.length > 0 && (
            <p style={{ margin: '0 0 0.5rem', fontSize: '0.85rem', color: 'var(--text-dim)' }}>
              {summaryParts.join(', ')}
            </p>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
            {results.map((r, i) => (
              <StatusChip key={i} result={r} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
