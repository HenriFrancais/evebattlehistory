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

describe('BulkUploader', () => {
  beforeEach(() => {
    vi.mocked(api.uploadLogs).mockReset()
  })

  it('selecting multiple files and submitting calls api.uploadLogs once with all files', async () => {
    const onUploaded = vi.fn()
    const files = [makeFile('log1.txt'), makeFile('log2.txt')]
    vi.mocked(api.uploadLogs).mockResolvedValue([
      makeResult({ filename: 'log1.txt' }),
      makeResult({ filename: 'log2.txt' }),
    ])

    render(<BulkUploader onUploaded={onUploaded} />)

    const input = screen.getByLabelText('Select log files')
    fireEvent.change(input, { target: { files } })

    const uploadBtn = screen.getByRole('button', { name: /upload/i })
    fireEvent.click(uploadBtn)

    await waitFor(() => expect(api.uploadLogs).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.uploadLogs)).toHaveBeenCalledWith(files)
    expect(onUploaded).toHaveBeenCalled()
  })

  it('renders chip-duplicate chip for a duplicate result', async () => {
    const onUploaded = vi.fn()
    vi.mocked(api.uploadLogs).mockResolvedValue([
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
    vi.mocked(api.uploadLogs).mockResolvedValue([
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
    vi.mocked(api.uploadLogs).mockResolvedValue([
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
