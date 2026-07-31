import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { emptyItem, type ChecklistItemDraft } from '../lib'
import { tourOptions } from '../test-utils'
import { ItemEditor } from './ItemEditor'

function Harness({
  initial,
  disabled = false,
  onItems,
}: {
  initial: ChecklistItemDraft[]
  disabled?: boolean
  onItems?: (items: ChecklistItemDraft[]) => void
}) {
  const [items, setItems] = useState(initial)
  return (
    <ItemEditor
      items={items}
      tours={tourOptions}
      disabled={disabled}
      onChange={(next) => {
        setItems(next)
        onItems?.(next)
      }}
    />
  )
}

function item(overrides: Partial<ChecklistItemDraft> = {}): ChecklistItemDraft {
  return { ...emptyItem(), title: 'Item', ...overrides }
}

describe('ItemEditor', () => {
  it('shows the tour picker only for start_tour and the url input only for open_url', async () => {
    render(<Harness initial={[item()]} />)

    expect(screen.queryByLabelText('Tour to start')).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Button action'), 'start_tour')
    const picker = screen.getByLabelText('Tour to start')
    expect(picker).toBeInTheDocument()
    // Draft tours stay selectable but are labelled as such.
    expect(screen.getByRole('option', { name: /Automation basics \(draft\)/ })).toBeInTheDocument()
    expect(screen.queryByLabelText('URL to open')).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Button action'), 'open_url')
    expect(screen.queryByLabelText('Tour to start')).not.toBeInTheDocument()
    expect(screen.getByLabelText('URL to open')).toBeInTheDocument()
  })

  it('shows completion-specific fields per completion type', async () => {
    render(<Harness initial={[item()]} />)

    expect(screen.queryByLabelText('URL pattern')).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Completes when'), 'tour_completed')
    expect(screen.getByLabelText('Tour that completes it')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Completes when'), 'url_visited')
    expect(screen.queryByLabelText('Tour that completes it')).not.toBeInTheDocument()
    expect(screen.getByLabelText('URL pattern')).toBeInTheDocument()
  })

  it('reorders items with the arrow buttons and disables the ends', async () => {
    render(<Harness initial={[item({ title: 'First' }), item({ title: 'Second' })]} />)

    expect(screen.getByRole('button', { name: 'Move item 1 up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move item 2 down' })).toBeDisabled()

    const titles = () =>
      screen.getAllByLabelText(/^Title$/).map((input) => (input as HTMLInputElement).value)
    expect(titles()).toEqual(['First', 'Second'])

    await userEvent.click(screen.getByRole('button', { name: 'Move item 2 up' }))
    expect(titles()).toEqual(['Second', 'First'])

    await userEvent.click(screen.getByRole('button', { name: 'Move item 1 down' }))
    expect(titles()).toEqual(['First', 'Second'])
  })

  it('adds and removes items, and caps adding at 20', async () => {
    render(<Harness initial={[]} />)
    expect(screen.getByText(/No items yet/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Add item/ }))
    expect(screen.getAllByTestId('checklist-item')).toHaveLength(1)
    expect(screen.getByText('1 of 20 items')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove item 1' }))
    expect(screen.queryAllByTestId('checklist-item')).toHaveLength(0)
  })

  it('caps the add button at the 20-item limit', () => {
    render(<Harness initial={Array.from({ length: 20 }, () => item())} />)
    expect(screen.getByRole('button', { name: /Add item/ })).toBeDisabled()
    expect(screen.getByText('20 of 20 items')).toBeInTheDocument()
  })

  it('disables every control when the viewer cannot manage tours', () => {
    render(<Harness initial={[item()]} disabled />)
    expect(screen.getAllByLabelText(/^Title$/)[0]).toBeDisabled()
    expect(screen.getByLabelText('Button action')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove item 1' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Add item/ })).toBeDisabled()
  })
})
