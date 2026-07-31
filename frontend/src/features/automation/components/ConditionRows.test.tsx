import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/helpers'

import { emptyConditionRow, serializeCondition, type ConditionRow } from '../lib'
import { ConditionRows } from './ConditionRows'

function Harness() {
  const [rows, setRows] = useState<ConditionRow[]>([emptyConditionRow()])
  return (
    <div>
      <ConditionRows rows={rows} onChange={setRows} />
      <pre data-testid="serialized">{JSON.stringify(rows.map(serializeCondition))}</pre>
    </div>
  )
}

describe('ConditionRows', () => {
  it('adds a row and serializes edited values', async () => {
    renderApp(<Harness />)
    expect(screen.getAllByTestId('condition-row')).toHaveLength(1)

    await userEvent.click(screen.getByRole('button', { name: /add condition/i }))
    expect(screen.getAllByTestId('condition-row')).toHaveLength(2)

    // First row defaults to status/eq; pick a value from its select.
    const values = screen.getAllByLabelText('Value')
    await userEvent.selectOptions(values[0]!, 'resolved')

    const serialized = JSON.parse(screen.getByTestId('serialized').textContent!)
    expect(serialized[0]).toEqual({ field: 'status', op: 'eq', value: 'resolved' })
  })

  it('removes a row', async () => {
    renderApp(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: /add condition/i }))
    expect(screen.getAllByTestId('condition-row')).toHaveLength(2)
    await userEvent.click(screen.getAllByRole('button', { name: /remove condition/i })[0]!)
    expect(screen.getAllByTestId('condition-row')).toHaveLength(1)
  })
})
