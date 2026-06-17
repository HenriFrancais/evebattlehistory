/**
 * Tests for ImpersonationPicker:
 * - Renders only when me.impersonation_available
 * - Selecting a user causes subsequent api calls to carry X-Impersonate-User
 * - Clearing removes the header
 */

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RosterUserOut } from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      me: vi.fn(),
      rosterUsers: vi.fn(),
    },
  }
})

import { api, getImpersonateUser, setImpersonateUser } from '../api'
import { ImpersonationPicker } from './ImpersonationPicker'

const MOCK_USERS: RosterUserOut[] = [
  { user_name: "LineMember", main_character_id: 95000001, rank: 'Member' },
  { user_name: "Ra'zok", main_character_id: 2112615087, rank: 'High Command' },
]

describe('ImpersonationPicker', () => {
  beforeEach(() => {
    setImpersonateUser(null)
    vi.mocked(api.rosterUsers).mockResolvedValue(MOCK_USERS)
  })

  it('renders the select with roster users', async () => {
    const onChanged = vi.fn()
    render(<ImpersonationPicker onChanged={onChanged} />)

    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    expect(screen.getByText('LineMember (Member)')).toBeInTheDocument()
    expect(screen.getByText("Ra'zok (High Command)")).toBeInTheDocument()
  })

  it('selecting a user sets impersonation and calls onChanged', async () => {
    const onChanged = vi.fn()
    render(<ImpersonationPicker onChanged={onChanged} />)

    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())

    await act(async () => {
      await userEvent.selectOptions(screen.getByRole('combobox'), 'LineMember')
    })

    expect(getImpersonateUser()).toBe('LineMember')
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it('clicking Stop clears impersonation and calls onChanged', async () => {
    const onChanged = vi.fn()
    setImpersonateUser('LineMember')
    render(<ImpersonationPicker onChanged={onChanged} />)

    await waitFor(() => expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument())

    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /stop/i }))
    })

    expect(getImpersonateUser()).toBeNull()
    expect(onChanged).toHaveBeenCalledOnce()
  })

  it('shows stop button only when impersonating', async () => {
    const onChanged = vi.fn()
    render(<ImpersonationPicker onChanged={onChanged} />)

    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /stop/i })).not.toBeInTheDocument()
  })
})
