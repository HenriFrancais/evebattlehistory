import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SourceComposer } from './SourceComposer'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      createBr: vi.fn(),
    },
  }
})

import { api } from '../api'

describe('SourceComposer', () => {
  beforeEach(() => {
    vi.mocked(api.createBr).mockReset()
  })

  it('submitting one Link row calls createBr with correct sources payload', async () => {
    const onCreated = vi.fn()
    vi.mocked(api.createBr).mockResolvedValue({ br_id: 'br42', status: 'pending' })
    render(<SourceComposer onCreated={onCreated} />)

    // Fill in the URL for the default link row
    fireEvent.change(screen.getByPlaceholderText(/zkillboard\.com/i), {
      target: { value: 'https://zkillboard.com/related/30004759/202606101800/' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('br42'))
    expect(vi.mocked(api.createBr)).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: [
          expect.objectContaining({
            kind: 'link',
            url: 'https://zkillboard.com/related/30004759/202606101800/',
          }),
        ],
      })
    )
  })

  it('submitting a Link + Window row builds correct payload with UTC ISO conversion', async () => {
    const onCreated = vi.fn()
    vi.mocked(api.createBr).mockResolvedValue({ br_id: 'br43', status: 'pending' })
    render(<SourceComposer onCreated={onCreated} />)

    // Row 0 is a link row already — fill in URL
    fireEvent.change(screen.getByPlaceholderText(/zkillboard\.com/i), {
      target: { value: 'https://zkillboard.com/related/30004759/202606101800/' },
    })

    // Add a second row
    fireEvent.click(screen.getByRole('button', { name: /Add source/i }))

    // Switch the second row to "window"
    const typeSelects = screen.getAllByRole('combobox', { name: /Source type/i })
    fireEvent.change(typeSelects[1], { target: { value: 'window' } })

    // Fill window row fields
    const systemInputs = screen.getAllByPlaceholderText(/system name/i)
    fireEvent.change(systemInputs[0], { target: { value: 'J125122' } })

    const startInputs = screen.getAllByLabelText(/start/i)
    fireEvent.change(startInputs[0], { target: { value: '2026-06-10T18:00' } })

    const endInputs = screen.getAllByLabelText(/end/i)
    fireEvent.change(endInputs[0], { target: { value: '2026-06-10T20:00' } })

    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('br43'))

    const call = vi.mocked(api.createBr).mock.calls[0][0]
    expect(call.sources).toHaveLength(2)
    expect(call.sources![0]).toMatchObject({ kind: 'link', url: 'https://zkillboard.com/related/30004759/202606101800/' })

    const win = call.sources![1]
    expect(win.kind).toBe('window')
    expect(win.system_name).toBe('J125122')
    // UTC ISO: 2026-06-10T18:00 treated as UTC → "2026-06-10T18:00:00.000Z"
    expect(win.window_start).toBe('2026-06-10T18:00:00.000Z')
    expect(win.window_end).toBe('2026-06-10T20:00:00.000Z')
  })

  it('rejects a window with start >= end client-side', async () => {
    const onCreated = vi.fn()
    render(<SourceComposer onCreated={onCreated} />)

    // Switch row 0 to window
    const typeSelect = screen.getByRole('combobox', { name: /Source type/i })
    fireEvent.change(typeSelect, { target: { value: 'window' } })

    const systemInputs = screen.getAllByPlaceholderText(/system name/i)
    fireEvent.change(systemInputs[0], { target: { value: 'J125122' } })

    const startInputs = screen.getAllByLabelText(/start/i)
    fireEvent.change(startInputs[0], { target: { value: '2026-06-10T20:00' } })

    const endInputs = screen.getAllByLabelText(/end/i)
    fireEvent.change(endInputs[0], { target: { value: '2026-06-10T18:00' } })

    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/start.*before.*end|before.*end|start < end/i)
    )
    expect(onCreated).not.toHaveBeenCalled()
    expect(vi.mocked(api.createBr)).not.toHaveBeenCalled()
  })

  it('rejects a link with a bad host client-side', async () => {
    const onCreated = vi.fn()
    render(<SourceComposer onCreated={onCreated} />)

    fireEvent.change(screen.getByPlaceholderText(/zkillboard\.com/i), {
      target: { value: 'https://example.com/related/123' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/zkillboard|evetools|unsupported|invalid/i)
    )
    expect(onCreated).not.toHaveBeenCalled()
    expect(vi.mocked(api.createBr)).not.toHaveBeenCalled()
  })

  it('cannot remove the last source row', () => {
    render(<SourceComposer onCreated={vi.fn()} />)
    // Only one row; remove button should be absent or disabled
    const removeButtons = screen.queryAllByRole('button', { name: /remove source|×/i })
    // Either no remove buttons at all, or the button is disabled
    if (removeButtons.length > 0) {
      expect(removeButtons[0]).toBeDisabled()
    } else {
      expect(removeButtons).toHaveLength(0)
    }
  })

  it('with an optional title, sends title in payload', async () => {
    const onCreated = vi.fn()
    vi.mocked(api.createBr).mockResolvedValue({ br_id: 'br44', status: 'pending' })
    render(<SourceComposer onCreated={onCreated} />)

    const titleInput = screen.getByLabelText(/title/i)
    await userEvent.type(titleInput, 'Home defence')

    fireEvent.change(screen.getByPlaceholderText(/zkillboard\.com/i), {
      target: { value: 'https://zkillboard.com/related/30004759/202606101800/' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('br44'))
    expect(vi.mocked(api.createBr)).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Home defence' })
    )
  })
})
