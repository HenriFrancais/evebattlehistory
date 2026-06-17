import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CreateBrForm } from './CreateBrForm'

// Mock api.createBr
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

describe('CreateBrForm', () => {
  beforeEach(() => {
    vi.mocked(api.createBr).mockReset()
  })

  it('rejects an unsupported URL host with inline error', async () => {
    const onCreated = vi.fn()
    render(<CreateBrForm onCreated={onCreated} />)
    fireEvent.change(screen.getByLabelText(/zKillboard or Aurora URL/i), {
      target: { value: 'https://example.com/related/123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/example\.com/)
    })
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('calls api.createBr with a valid zkillboard URL', async () => {
    const onCreated = vi.fn()
    vi.mocked(api.createBr).mockResolvedValue({ br_id: 'newbr1', status: 'pending' })
    render(<CreateBrForm onCreated={onCreated} />)
    fireEvent.change(screen.getByLabelText(/zKillboard or Aurora URL/i), {
      target: { value: 'https://zkillboard.com/related/30004759/202606101800/' },
    })
    fireEvent.change(screen.getByLabelText(/Title/i), {
      target: { value: 'My Test BR' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('newbr1'))
    expect(vi.mocked(api.createBr)).toHaveBeenCalledWith({
      url: 'https://zkillboard.com/related/30004759/202606101800/',
      title: 'My Test BR',
    })
  })

  it('calls api.createBr with a valid Aurora URL', async () => {
    const onCreated = vi.fn()
    vi.mocked(api.createBr).mockResolvedValue({ br_id: 'newbr2', status: 'pending' })
    render(<CreateBrForm onCreated={onCreated} />)
    fireEvent.change(screen.getByLabelText(/zKillboard or Aurora URL/i), {
      target: { value: 'https://br.evetools.org/br/abc123' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create BR/i }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('newbr2'))
    expect(vi.mocked(api.createBr)).toHaveBeenCalledWith({
      url: 'https://br.evetools.org/br/abc123',
      title: undefined,
    })
  })
})
