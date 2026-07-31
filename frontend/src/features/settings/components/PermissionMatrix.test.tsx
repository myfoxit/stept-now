import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/helpers'

import { groupPermissions, PermissionMatrix } from './PermissionMatrix'

function Harness({ initial = [] as string[] }) {
  const [selected, setSelected] = useState<string[]>(initial)
  return (
    <div>
      <PermissionMatrix
        all={['conversations:read', 'contacts:write']}
        selected={selected}
        onChange={setSelected}
      />
      <pre data-testid="selected">{selected.join(',')}</pre>
    </div>
  )
}

describe('groupPermissions', () => {
  it('groups permissions by resource prefix', () => {
    expect(groupPermissions(['a:read', 'a:write', 'b:read'])).toEqual({
      a: ['a:read', 'a:write'],
      b: ['b:read'],
    })
  })
})

describe('PermissionMatrix', () => {
  it('toggles a permission on and off', async () => {
    renderApp(<Harness />)
    expect(screen.getByTestId('selected').textContent).toBe('')

    await userEvent.click(screen.getByLabelText('read'))
    expect(screen.getByTestId('selected').textContent).toBe('conversations:read')

    await userEvent.click(screen.getByLabelText('read'))
    expect(screen.getByTestId('selected').textContent).toBe('')
  })
})
