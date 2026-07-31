import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/helpers'

import { type StepDraft } from '../lib'
import { StepEditor } from './StepEditor'

function Harness() {
  const [steps, setSteps] = useState<StepDraft[]>([
    { id: 's1', selector: '#one', title: 'One', body: '', placement: 'auto' },
    { id: 's2', selector: '#two', title: 'Two', body: '', placement: 'auto' },
  ])
  return (
    <div>
      <StepEditor steps={steps} onChange={setSteps} />
      <pre data-testid="order">{steps.map((s) => s.selector).join(',')}</pre>
    </div>
  )
}

describe('StepEditor', () => {
  it('reorders steps when moving one down', async () => {
    renderApp(<Harness />)
    expect(screen.getByTestId('order').textContent).toBe('#one,#two')

    await userEvent.click(screen.getByRole('button', { name: /move step 1 down/i }))
    expect(screen.getByTestId('order').textContent).toBe('#two,#one')
  })

  it('adds and removes steps', async () => {
    renderApp(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: /add step/i }))
    expect(screen.getAllByTestId('tour-step')).toHaveLength(3)

    await userEvent.click(screen.getByRole('button', { name: /remove step 3/i }))
    expect(screen.getAllByTestId('tour-step')).toHaveLength(2)
  })
})
