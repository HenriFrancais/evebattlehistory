import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogUploadResult } from '../api'
import { BulkUploader } from './BulkUploader'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      uploadLogs: vi.fn(),
    },
  }
})

import { api } from '../api'

function makeFile(name: string): File {
  return new File(['log content'], name, { type: 'text/plain' })
}

function makeResult(overrides: Partial<LogUploadResult> = {}): LogUploadResult {
  return {
    filename: 'test.txt',
    file_id: 'file1',
    status: 'parsed',
    event_count: 10,
    character_name: 'TestChar',
    message: null,
    ...overrides,
  }
}

/** Mock the per-file upload: each call carries ONE file; answer by its name. */
function mockUploads(results: LogUploadResult[]) {
  vi.mocked(api.uploadLogs).mockImplementation(async (files: File[]) => {
    const r = results.find((x) => x.filename === files[0].name)
    if (!r) throw new Error('network down')
    return [r]
  })
}

describe('BulkUploader', () => {
  beforeEach(() => {
    vi.mocked(api.uploadLogs).mockReset()
  })

  it('uploads each selected file in its own request', async () => {
    const onUploaded = vi.fn()
    const files = [makeFile('log1.txt'), makeFile('log2.txt')]
    mockUploads([makeResult({ filename: 'log1.txt' }), makeResult({ filename: 'log2.txt' })])

    render(<BulkUploader onUploaded={onUploaded} />)

    const input = screen.getByLabelText('Select log files')
    fireEvent.change(input, { target: { files } })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await waitFor(() => expect(api.uploadLogs).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.uploadLogs).mock.calls.map((c) => c[0])).toEqual([[files[0]], [files[1]]])
    await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1))
  })

  it('shows progress while files are still uploading', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    vi.mocked(api.uploadLogs).mockImplementation(async (files: File[]) => {
      if (files[0].name === 'b.txt') await gate
      return [makeResult({ filename: files[0].name })]
    })

    render(<BulkUploader onUploaded={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Select log files'), {
      target: { files: [makeFile('a.txt'), makeFile('b.txt')] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    // First file is done and already shown; second is in flight.
    await waitFor(() => expect(screen.getByText(/1 of 2/)).toBeInTheDocument())
    expect(screen.getAllByText(/parsed/, { selector: '.chip-parsed' })).toHaveLength(1)
    release()
    await waitFor(() => expect(screen.getByText(/2 parsed/i)).toBeInTheDocument())
  })

  it('a file whose request fails becomes an error chip and the rest still upload', async () => {
    mockUploads([makeResult({ filename: 'ok.txt' })]) // bad.txt → rejects
    render(<BulkUploader onUploaded={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Select log files'), {
      target: { files: [makeFile('bad.txt'), makeFile('ok.txt')] },
    })
    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await waitFor(() => {
      expect(screen.getByText(/bad\.txt: network down/)).toHaveClass('chip-error')
      expect(screen.getByText(/1 parsed/i)).toBeInTheDocument()
    })
  })

  it('renders chip-duplicate chip for a duplicate result', async () => {
    const onUploaded = vi.fn()
    mockUploads([
      makeResult({ filename: 'dup.txt', status: 'duplicate', file_id: null }),
    ])

    render(<BulkUploader onUploaded={onUploaded} />)

    const input = screen.getByLabelText('Select log files')
    fireEvent.change(input, { target: { files: [makeFile('dup.txt')] } })

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await waitFor(() => {
      const chip = screen.getByText(/already uploaded/i)
      expect(chip).toHaveClass('chip-duplicate')
    })
  })

  it('renders chip-parsed, chip-unresolved, chip-error chips', async () => {
    const onUploaded = vi.fn()
    mockUploads([
      makeResult({ filename: 'a.txt', status: 'parsed' }),
      makeResult({ filename: 'b.txt', status: 'unresolved', file_id: null, character_name: null }),
      makeResult({ filename: 'c.txt', status: 'error', file_id: null, message: 'parse failed' }),
    ])

    render(<BulkUploader onUploaded={onUploaded} />)

    const input = screen.getByLabelText('Select log files')
    fireEvent.change(input, {
      target: { files: [makeFile('a.txt'), makeFile('b.txt'), makeFile('c.txt')] },
    })

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await waitFor(() => {
      expect(screen.getByText(/character not matched/i)).toHaveClass('chip-unresolved')
      expect(screen.getByText(/parse failed/i)).toHaveClass('chip-error')
    })
    // parsed chip — text contains 'parsed'
    const parsedChip = screen.getByText(/TestChar.*parsed|parsed/i, { selector: '.chip-parsed' })
    expect(parsedChip).toBeInTheDocument()
  })

  it('shows summary counts', async () => {
    const onUploaded = vi.fn()
    mockUploads([
      makeResult({ filename: 'a.txt', status: 'parsed' }),
      makeResult({ filename: 'b.txt', status: 'parsed' }),
      makeResult({ filename: 'c.txt', status: 'duplicate', file_id: null }),
    ])

    render(<BulkUploader onUploaded={onUploaded} />)

    const input = screen.getByLabelText('Select log files')
    fireEvent.change(input, {
      target: { files: [makeFile('a.txt'), makeFile('b.txt'), makeFile('c.txt')] },
    })

    fireEvent.click(screen.getByRole('button', { name: /upload/i }))

    await waitFor(() => {
      expect(screen.getByText(/2 parsed/i)).toBeInTheDocument()
      expect(screen.getByText(/1 duplicate/i)).toBeInTheDocument()
    })
  })
})
