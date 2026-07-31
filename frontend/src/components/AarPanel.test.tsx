import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AarPanel as AarPanelData } from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getAar: vi.fn(),
      putAar: vi.fn(),
      deleteAar: vi.fn(),
      createAarComment: vi.fn(),
      editAarComment: vi.fn(),
      deleteAarComment: vi.fn(),
      toggleAarReaction: vi.fn(),
    },
  }
})
import { api } from '../api'
import { AarPanel } from './AarPanel'

const REACTIONS = ['👍', '❤️', '🔥', '🎉', '😂', 'o7']

function emptyPanel(canManage: boolean): AarPanelData {
  return {
    aar: null,
    comments: [],
    reactions: [],
    viewer: { user_name: 'Ra', can_manage: canManage, allowed_reactions: REACTIONS },
  }
}

function populatedPanel(canManage: boolean): AarPanelData {
  return {
    aar: {
      aar_id: 42,
      body: '# Debrief\n**Held** the gate.',
      created_by_user: 'Ra',
      created_by_char_id: 1,
      updated_by_user: 'Ra',
      created_at: '2026-07-12T00:00:00Z',
      updated_at: '2026-07-12T00:05:00Z',
    },
    comments: [
      {
        comment_id: 7,
        author_user: 'LineMember',
        author_char_id: 2,
        body: 'gf all',
        created_at: '2026-07-12T00:10:00Z',
        updated_at: null,
        editable: false,
        deletable: canManage,
        reactions: [],
      },
    ],
    reactions: [],
    viewer: { user_name: 'Ra', can_manage: canManage, allowed_reactions: REACTIONS },
  }
}

describe('AarPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders the AAR markdown body (heading + bold)', async () => {
    vi.mocked(api.getAar).mockResolvedValue(populatedPanel(false))
    render(<AarPanel brId="br1" canManage={false} />)

    await waitFor(() => expect(screen.getByTestId('aar-body')).toBeInTheDocument())
    // Markdown → real elements: a heading and a <strong>.
    expect(screen.getByRole('heading', { name: 'Debrief' })).toBeInTheDocument()
    expect(screen.getByText('Held').tagName).toBe('STRONG')
    // Comment is shown too.
    expect(screen.getByText('gf all')).toBeInTheDocument()
  })

  it('shows edit/delete controls only for managers', async () => {
    vi.mocked(api.getAar).mockResolvedValue(populatedPanel(true))
    const { unmount } = render(<AarPanel brId="br1" canManage={true} />)
    await waitFor(() => expect(screen.getByTestId('aar-body')).toBeInTheDocument())
    expect(screen.getByTestId('aar-edit-btn')).toBeInTheDocument()
    expect(screen.getByTestId('aar-delete-btn')).toBeInTheDocument()
    unmount()

    vi.mocked(api.getAar).mockResolvedValue(populatedPanel(false))
    render(<AarPanel brId="br1" canManage={false} />)
    await waitFor(() => expect(screen.getByTestId('aar-body')).toBeInTheDocument())
    expect(screen.queryByTestId('aar-edit-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('aar-delete-btn')).not.toBeInTheDocument()
  })

  it('offers "Write an AAR" only to managers when empty', async () => {
    vi.mocked(api.getAar).mockResolvedValue(emptyPanel(true))
    const { unmount } = render(<AarPanel brId="br1" canManage={true} />)
    await waitFor(() => expect(screen.getByTestId('aar-write-btn')).toBeInTheDocument())
    unmount()

    vi.mocked(api.getAar).mockResolvedValue(emptyPanel(false))
    render(<AarPanel brId="br1" canManage={false} />)
    await waitFor(() =>
      expect(screen.getByText(/No After Action Report/i)).toBeInTheDocument(),
    )
    expect(screen.queryByTestId('aar-write-btn')).not.toBeInTheDocument()
  })

  it('posts a comment via the api', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getAar).mockResolvedValue(populatedPanel(false))
    vi.mocked(api.createAarComment).mockResolvedValue({
      comment_id: 8, author_user: 'Ra', author_char_id: 1, body: 'nice',
      created_at: 't', updated_at: null, editable: true, deletable: true, reactions: [],
    })
    render(<AarPanel brId="br1" canManage={false} />)
    await waitFor(() => expect(screen.getByTestId('aar-comment-input')).toBeInTheDocument())

    await user.type(screen.getByTestId('aar-comment-input'), 'nice')
    await user.click(screen.getByTestId('aar-comment-post-btn'))

    await waitFor(() =>
      expect(api.createAarComment).toHaveBeenCalledWith('br1', 'nice'),
    )
  })

  it('toggles a reaction and shows the updated count', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getAar).mockResolvedValue(populatedPanel(false))
    vi.mocked(api.toggleAarReaction).mockResolvedValue({
      target_type: 'comment',
      target_id: 7,
      reactions: [{ emoji: 'o7', count: 1, reacted_by_me: true, user_names: ['Ra'] }],
    })
    render(<AarPanel brId="br1" canManage={false} />)
    await waitFor(() => expect(screen.getByTestId('aar-body')).toBeInTheDocument())

    // Open the comment's reaction palette and pick o7.
    const bar = screen.getByTestId('aar-reactions-comment-7')
    await user.click(within(bar).getByLabelText('Add reaction'))
    await user.click(within(bar).getByText('o7'))

    await waitFor(() =>
      expect(api.toggleAarReaction).toHaveBeenCalledWith('br1', 'comment', 7, 'o7'),
    )
    // The returned group renders as a count chip.
    await waitFor(() => expect(within(bar).getByText('1')).toBeInTheDocument())
  })
})
