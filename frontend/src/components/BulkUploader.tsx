// Drop zone + multi-file input; pick one or more log files (or drag & drop them).
// Files are uploaded ONE PER REQUEST, in order, so a whole Gamelogs folder is many
// short requests (no proxy timeout) with live progress: each file's status chip
// appears as soon as it finishes, and one failed file never aborts the rest.
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
  if (result.status === 'empty') {
    return <span className="chip chip-empty">{result.filename}: no combat in this file</span>
  }
  // error
  return <span className="chip chip-error">{result.filename}: {result.message ?? 'error'}</span>
}

export function BulkUploader({ onUploaded }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [results, setResults] = useState<LogUploadResult[] | null>(null)
  const [uploading, setUploading] = useState(false)
  const [total, setTotal] = useState(0)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function handleFiles(newFiles: FileList | File[]) {
    const arr = Array.from(newFiles)
    setFiles(arr)
    setResults(null)
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
    const queue = files
    setUploading(true)
    setTotal(queue.length)
    setResults([])
    for (const file of queue) {
      let result: LogUploadResult
      try {
        result = (await api.uploadLogs([file]))[0]
      } catch (e: unknown) {
        result = {
          filename: file.name,
          file_id: null,
          status: 'error',
          event_count: 0,
          character_name: null,
          message: String((e as Error)?.message ?? e),
        }
      }
      setResults((prev) => [...(prev ?? []), result])
    }
    setUploading(false)
    onUploaded()
  }

  const parsedCount = results?.filter((r) => r.status === 'parsed').length ?? 0
  const duplicateCount = results?.filter((r) => r.status === 'duplicate').length ?? 0
  const unresolvedCount = results?.filter((r) => r.status === 'unresolved').length ?? 0
  const emptyCount = results?.filter((r) => r.status === 'empty').length ?? 0
  const errorCount = results?.filter((r) => r.status === 'error').length ?? 0

  const summaryParts: string[] = []
  if (parsedCount > 0) summaryParts.push(`${parsedCount} parsed`)
  if (duplicateCount > 0) summaryParts.push(`${duplicateCount} duplicate${duplicateCount !== 1 ? 's' : ''}`)
  if (unresolvedCount > 0) summaryParts.push(`${unresolvedCount} unresolved`)
  if (emptyCount > 0) summaryParts.push(`${emptyCount} with no combat`)
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
        <p style={{ margin: 0, color: 'var(--text-dim)' }}>Select log files or drag &amp; drop them</p>
        {files.length > 0 && (
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.85rem' }}>
            {files.length} file{files.length !== 1 ? 's' : ''} selected
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
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
        {uploading && (
          <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }} role="status">
            {results?.length ?? 0} of {total} done
          </span>
        )}
      </div>

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
