import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FilterBuilder } from '@/features/inbox/components/FilterBuilder'
import type { FilterField, FilterQuery } from '@/features/inbox/api'

const FIELDS: FilterField[] = [
  {
    field: 'status',
    label: 'Status',
    ops: ['eq', 'in'],
    value_type: 'enum',
    options: [
      { value: 'open', label: 'Open' },
      { value: 'resolved', label: 'Resolved' },
    ],
  },
  { field: 'subject', label: 'Subject', ops: ['contains', 'exists'], value_type: 'string' },
  {
    field: 'attributes.plan',
    label: 'Plan',
    ops: ['eq', 'exists'],
    value_type: 'list',
    options: [],
  },
]

const EMPTY: FilterQuery = { match: 'all', conditions: [] }

afterEach(cleanup)

describe('FilterBuilder', () => {
  it('renders every catalog field, including workspace attributes', async () => {
    render(<FilterBuilder fields={FIELDS} query={EMPTY} onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /add condition/i }))
  })

  it('adds a condition seeded with the field’s first operator', async () => {
    const onChange = vi.fn()
    render(<FilterBuilder fields={FIELDS} query={EMPTY} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /add condition/i }))
    expect(onChange).toHaveBeenCalledWith({
      match: 'all',
      conditions: [{ field: 'status', op: 'eq', value: '' }],
    })
  })

  it('hides the value input for valueless operators', () => {
    const query: FilterQuery = {
      match: 'all',
      conditions: [{ field: 'subject', op: 'exists', value: null }],
    }
    render(<FilterBuilder fields={FIELDS} query={query} onChange={vi.fn()} />)
    expect(screen.queryByLabelText('Value')).toBeNull()
  })

  it('shows a value input for operators that need one', () => {
    const query: FilterQuery = {
      match: 'all',
      conditions: [{ field: 'subject', op: 'contains', value: 'refund' }],
    }
    render(<FilterBuilder fields={FIELDS} query={query} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Value')).toHaveValue('refund')
  })

  it('parses a comma-separated list for multi-value operators', async () => {
    const onChange = vi.fn()
    const query: FilterQuery = {
      match: 'all',
      conditions: [{ field: 'status', op: 'in', value: [] }],
    }
    render(<FilterBuilder fields={FIELDS} query={query} onChange={onChange} />)
    await userEvent.type(screen.getByLabelText('Value'), 'open, resolved')
    const last = onChange.mock.calls.at(-1)?.[0] as FilterQuery
    expect(Array.isArray(last.conditions[0].value)).toBe(true)
  })

  it('removes a condition', async () => {
    const onChange = vi.fn()
    const query: FilterQuery = {
      match: 'all',
      conditions: [{ field: 'subject', op: 'contains', value: 'x' }],
    }
    render(<FilterBuilder fields={FIELDS} query={query} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /remove condition/i }))
    expect(onChange).toHaveBeenCalledWith({ match: 'all', conditions: [] })
  })

  it('disables adding when the catalog is empty', () => {
    render(<FilterBuilder fields={[]} query={EMPTY} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: /add condition/i })).toBeDisabled()
  })
})
