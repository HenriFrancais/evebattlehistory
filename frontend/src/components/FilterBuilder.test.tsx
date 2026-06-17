import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FilterGroup } from '../api'
import { FilterBuilder } from './FilterBuilder'

describe('FilterBuilder', () => {
  it('builds a 2-condition AND tree (numeric + ship) and calls onApply with exact tree', async () => {
    const onApply = vi.fn()
    render(<FilterBuilder scope="br" onApply={onApply} />)

    // Row 0: select field "our_isk_destroyed", op ">=", value "50"
    // Default row type is "field" and field is already "our_isk_destroyed"
    // Default op is already ">="
    const valueInput = screen.getByTestId('filter-row-0-value')
    await userEvent.clear(valueInput)
    await userEvent.type(valueInput, '50')

    // Add a second row
    fireEvent.click(screen.getByTestId('filter-add-row'))

    // Row 1: change type to ship
    fireEvent.change(screen.getByTestId('filter-row-1-type'), { target: { value: 'ship' } })

    // Fill ship name
    const shipInput = screen.getByTestId('filter-row-1-ship')
    await userEvent.clear(shipInput)
    await userEvent.type(shipInput, 'Lif')

    // Ship op is already ">="
    // Fill count
    const countInput = screen.getByTestId('filter-row-1-count')
    await userEvent.clear(countInput)
    await userEvent.type(countInput, '1')

    // Side is already "friendly"

    // Click Apply
    fireEvent.click(screen.getByTestId('filter-apply'))

    expect(onApply).toHaveBeenCalledOnce()
    const tree = onApply.mock.calls[0][0] as FilterGroup
    expect(tree.op).toBe('and')
    expect(tree.clauses).toHaveLength(2)
    expect(tree.clauses[0]).toEqual({
      field: 'our_isk_destroyed',
      op: '>=',
      value: 50_000_000_000,
    })
    expect(tree.clauses[1]).toEqual({
      field: 'ship_fielded',
      ship: 'Lif',
      op: '>=',
      count: 1,
      side: 'friendly',
    })
  })

  it('switching scope changes available fields', () => {
    const { unmount } = render(<FilterBuilder scope="fight" onApply={vi.fn()} />)
    // Fight scope should have isk_destroyed_total as an option
    const fightSelect = screen.getByTestId('filter-row-0-field')
    const fightOptions = Array.from(fightSelect.querySelectorAll('option')).map((o) => o.value)
    expect(fightOptions).toContain('isk_destroyed_total')
    expect(fightOptions).not.toContain('our_isk_destroyed')
    unmount()

    render(<FilterBuilder scope="br" onApply={vi.fn()} />)
    const brSelect = screen.getByTestId('filter-row-0-field')
    const brOptions = Array.from(brSelect.querySelectorAll('option')).map((o) => o.value)
    expect(brOptions).toContain('our_isk_destroyed')
    expect(brOptions).not.toContain('isk_destroyed_total')
  })

  it('result field shows enum select with win/loss/tie options', () => {
    render(<FilterBuilder scope="br" onApply={vi.fn()} />)

    // Change field to "result"
    fireEvent.change(screen.getByTestId('filter-row-0-field'), { target: { value: 'result' } })

    // Value widget should be a select with win/loss/tie options
    const valueSelect = screen.getByTestId('filter-row-0-value')
    expect(valueSelect.tagName).toBe('SELECT')
    const opts = Array.from(valueSelect.querySelectorAll('option')).map((o) => o.value)
    expect(opts).toContain('win')
    expect(opts).toContain('loss')
    expect(opts).toContain('tie')
  })

  it('capitals_involved boolean field emits native boolean true in tree', async () => {
    const onApply = vi.fn()
    render(<FilterBuilder scope="fight" onApply={onApply} />)

    // Change field to "capitals_involved"
    fireEvent.change(screen.getByTestId('filter-row-0-field'), { target: { value: 'capitals_involved' } })

    // Value is a select with "true"/"false"; default shows "true" first
    const valueSelect = screen.getByTestId('filter-row-0-value')
    fireEvent.change(valueSelect, { target: { value: 'true' } })

    fireEvent.click(screen.getByTestId('filter-apply'))

    expect(onApply).toHaveBeenCalledOnce()
    const tree = onApply.mock.calls[0][0] as FilterGroup
    expect(tree.clauses).toHaveLength(1)
    expect(tree.clauses[0]).toEqual({
      field: 'capitals_involved',
      op: '==',
      value: true,
    })
  })

  it('fight scope ship condition builds ship_count leaf', async () => {
    const onApply = vi.fn()
    render(<FilterBuilder scope="fight" onApply={onApply} />)

    // Change row 0 type to "ship"
    fireEvent.change(screen.getByTestId('filter-row-0-type'), { target: { value: 'ship' } })

    const shipInput = screen.getByTestId('filter-row-0-ship')
    await userEvent.clear(shipInput)
    await userEvent.type(shipInput, 'Bhaalgorn')

    // op stays ">="
    const countInput = screen.getByTestId('filter-row-0-count')
    await userEvent.clear(countInput)
    await userEvent.type(countInput, '6')

    // side to "any"
    fireEvent.change(screen.getByTestId('filter-row-0-side'), { target: { value: 'any' } })

    fireEvent.click(screen.getByTestId('filter-apply'))

    expect(onApply).toHaveBeenCalledOnce()
    const tree = onApply.mock.calls[0][0] as FilterGroup
    expect(tree.op).toBe('and')
    expect(tree.clauses).toHaveLength(1)
    expect(tree.clauses[0]).toEqual({
      field: 'ship_count',
      ship: 'Bhaalgorn',
      op: '>=',
      count: 6,
      side: 'any',
    })
  })
})
