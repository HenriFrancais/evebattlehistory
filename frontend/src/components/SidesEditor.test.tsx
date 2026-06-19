import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrSides } from '../api'
import { SidesEditor } from './SidesEditor'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, api: { ...actual.api, getSides: vi.fn(), setSide: vi.fn() } }
})
import { api } from '../api'

const sides: BrSides = {
  can_edit: true,
  entities: [
    { entity_type: 'alliance', entity_id: 1, name: 'No Vacancies.', side: 'friendly', overridden: false, baseline: true },
    { entity_type: 'alliance', entity_id: 2, name: 'Mystery Alliance', side: 'unassigned', overridden: false, baseline: false },
  ],
}

describe('SidesEditor', () => {
  beforeEach(() => {
    vi.mocked(api.getSides).mockReset()
    vi.mocked(api.setSide).mockReset()
  })

  it('renders three columns and places entities by side', async () => {
    vi.mocked(api.getSides).mockResolvedValue(sides)
    render(<SidesEditor brId="b1" />)
    await waitFor(() => expect(screen.getByTestId('sides-editor')).toBeInTheDocument())
    expect(screen.getByText(/Friendly/)).toBeInTheDocument()
    expect(screen.getByText(/Unassigned/)).toBeInTheDocument()
    expect(screen.getByText(/Hostile/)).toBeInTheDocument()
    expect(screen.getByText('No Vacancies.')).toBeInTheDocument()
    expect(screen.getByText('Mystery Alliance')).toBeInTheDocument()
    expect(screen.queryByText('blue')).not.toBeInTheDocument() // no blue tag anymore
  })

  it('moving an unassigned entity right calls setSide(hostile) and fires onChange', async () => {
    vi.mocked(api.getSides).mockResolvedValue(sides)
    vi.mocked(api.setSide).mockResolvedValue({
      ...sides,
      entities: [sides.entities[0], { ...sides.entities[1], side: 'hostile', overridden: true }],
    })
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<SidesEditor brId="b1" onChange={onChange} />)
    await waitFor(() => expect(screen.getByTestId('sides-editor')).toBeInTheDocument())

    await user.click(screen.getByTitle('Move to hostile'))
    expect(api.setSide).toHaveBeenCalledWith('b1', { entity_type: 'alliance', entity_id: 2, side: 'hostile' })
    await waitFor(() => expect(onChange).toHaveBeenCalled())
  })

  it('read-only when not editable', async () => {
    vi.mocked(api.getSides).mockResolvedValue({ ...sides, can_edit: false })
    render(<SidesEditor brId="b1" />)
    await waitFor(() => expect(screen.getByTestId('sides-editor')).toBeInTheDocument())
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText(/managed by FC/i)).toBeInTheDocument()
  })
})
