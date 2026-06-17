import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BrStatus } from '../api'
import { api } from '../api'
import { IngestProgress } from './IngestProgress'

// Mock the api module so no real fetch happens
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getBrStatus: vi.fn(),
    },
  }
})

function makeStatus(overrides: Partial<BrStatus> = {}): BrStatus {
  return {
    br_id: 'br1',
    status: 'ingesting',
    progress_pct: 45,
    error_text: null,
    ...overrides,
  }
}

describe('IngestProgress', () => {
  it('shows progress bar and percent for non-ready status', () => {
    render(<IngestProgress brId="br1" initialStatus={makeStatus({ progress_pct: 45 })} />)
    expect(screen.getByTestId('ingest-progress')).toBeInTheDocument()
    expect(screen.getByText(/45%/)).toBeInTheDocument()
  })

  it('renders nothing when status is already ready', () => {
    const { container } = render(
      <IngestProgress brId="br1" initialStatus={makeStatus({ status: 'ready', progress_pct: 100 })} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows error message on error status', () => {
    render(
      <IngestProgress
        brId="br1"
        initialStatus={makeStatus({ status: 'error', error_text: 'fetch failed' })}
      />
    )
    expect(screen.getByText(/fetch failed/)).toBeInTheDocument()
  })

  it('stops polling after terminal status is returned', async () => {
    vi.useFakeTimers()
    const getBrStatus = vi.mocked(api.getBrStatus)
    getBrStatus.mockResolvedValueOnce(makeStatus({ status: 'ready', progress_pct: 100 }))

    render(<IngestProgress brId="br1" initialStatus={makeStatus({ status: 'ingesting', progress_pct: 50 })} />)

    // Advance past the first poll (2s)
    await act(async () => {
      vi.advanceTimersByTime(2000)
      // Flush the resolved promise
      await Promise.resolve()
    })

    const callsAfterTerminal = getBrStatus.mock.calls.length
    expect(callsAfterTerminal).toBe(1)

    // Advance another 6 seconds — no further polls should occur
    await act(async () => {
      vi.advanceTimersByTime(6000)
      await Promise.resolve()
    })

    expect(getBrStatus.mock.calls.length).toBe(callsAfterTerminal)

    vi.useRealTimers()
  })
})
