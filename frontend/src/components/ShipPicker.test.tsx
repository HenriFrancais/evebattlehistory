import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ShipPicker } from './ShipPicker'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, searchShipTypes: vi.fn(), setParticipantShip: vi.fn() },
  }
})
import { api } from '../api'

describe('ShipPicker', () => {
  beforeEach(() => {
    vi.mocked(api.searchShipTypes).mockReset()
    vi.mocked(api.setParticipantShip).mockReset()
  })

  it('searches and assigns a ship via the API', async () => {
    vi.mocked(api.searchShipTypes).mockResolvedValue([{ type_id: 11987, name: 'Guardian' }])
    vi.mocked(api.setParticipantShip).mockResolvedValue({ ok: true })
    const onChanged = vi.fn()
    const user = userEvent.setup()
    render(<ShipPicker brId="br1" characterId={42} currentShipTypeId={null} onChanged={onChanged} />)

    await user.click(screen.getByTestId('ship-picker-42'))  // "set ship"
    await user.type(screen.getByPlaceholderText('Search ship…'), 'guar')
    await waitFor(() => expect(api.searchShipTypes).toHaveBeenCalledWith('guar'))
    await user.click(await screen.findByText('Guardian'))

    expect(api.setParticipantShip).toHaveBeenCalledWith('br1', 42, 11987)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})
