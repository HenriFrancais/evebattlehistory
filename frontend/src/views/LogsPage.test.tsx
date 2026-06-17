import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MyLogFile } from '../api'
import { LogsPage } from './LogsPage'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      myLogs: vi.fn(),
      uploadLogs: vi.fn(),
    },
  }
})

import { api } from '../api'

const mockLogs: MyLogFile[] = [
  {
    file_id: 'f1',
    filename: 'combat-2026.01.01.txt',
    character_id: 111,
    character_name: 'AlphaChar',
    listener_name: 'AlphaChar',
    parse_status: 'parsed',
    event_count: 42,
    log_start_at: '2026-01-01T10:00:00Z',
    log_end_at: '2026-01-01T11:00:00Z',
    uploaded_at: '2026-01-02T09:00:00Z',
  },
  {
    file_id: 'f2',
    filename: 'combat-2026.01.02.txt',
    character_id: null,
    character_name: null,
    listener_name: null,
    parse_status: 'unresolved',
    event_count: 0,
    log_start_at: null,
    log_end_at: null,
    uploaded_at: '2026-01-02T09:01:00Z',
  },
  {
    file_id: 'f3',
    filename: 'combat-2026.01.03.txt',
    character_id: 222,
    character_name: 'BetaChar',
    listener_name: 'BetaChar',
    parse_status: 'duplicate',
    event_count: 15,
    log_start_at: '2026-01-03T10:00:00Z',
    log_end_at: '2026-01-03T11:00:00Z',
    uploaded_at: '2026-01-04T09:00:00Z',
  },
]

describe('LogsPage', () => {
  beforeEach(() => {
    vi.mocked(api.myLogs).mockReset()
    vi.mocked(api.uploadLogs).mockReset()
  })

  it('shows logs-table with character names', async () => {
    vi.mocked(api.myLogs).mockResolvedValue(mockLogs)
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <LogsPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByTestId('logs-table')).toBeInTheDocument())

    // AlphaChar appears in both character and listener columns
    expect(screen.getAllByText('AlphaChar').length).toBeGreaterThanOrEqual(1)
    // BetaChar appears in both character and listener columns
    expect(screen.getAllByText('BetaChar').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('combat-2026.01.01.txt')).toBeInTheDocument()
    expect(screen.getByText('combat-2026.01.02.txt')).toBeInTheDocument()
    expect(screen.getByText('combat-2026.01.03.txt')).toBeInTheDocument()
  })

  it('shows status chips for each row', async () => {
    vi.mocked(api.myLogs).mockResolvedValue(mockLogs)
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <LogsPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByTestId('logs-table')).toBeInTheDocument())

    // Check chip classes exist
    expect(document.querySelector('.chip-parsed')).toBeInTheDocument()
    expect(document.querySelector('.chip-unresolved')).toBeInTheDocument()
    expect(document.querySelector('.chip-duplicate')).toBeInTheDocument()
  })

  it('shows empty state when no logs', async () => {
    vi.mocked(api.myLogs).mockResolvedValue([])
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <LogsPage />
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByText('No logs uploaded yet.')).toBeInTheDocument())
    expect(screen.queryByTestId('logs-table')).not.toBeInTheDocument()
  })
})
