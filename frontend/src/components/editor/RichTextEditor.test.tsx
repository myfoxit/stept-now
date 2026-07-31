import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { RichTextEditor } from './RichTextEditor'
import { installEditorDomShims } from './test-dom'

installEditorDomShims()

/** Controlled harness mirroring how features embed the editor. */
function Harness({
  initial = '',
  variant = 'full' as const,
  onChange,
}: {
  initial?: string
  variant?: 'full' | 'compact'
  onChange?: (md: string) => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <RichTextEditor
        value={value}
        variant={variant}
        onChange={(md) => {
          setValue(md)
          onChange?.(md)
        }}
      />
      <pre data-testid="markdown">{value}</pre>
    </>
  )
}

describe('RichTextEditor', () => {
  it('renders the incoming markdown as a rich document', () => {
    render(<Harness initial={'# Title\n\nHello **world**'} />)
    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    expect(region.querySelector('h1')?.textContent).toBe('Title')
    expect(region.querySelector('strong')?.textContent).toBe('world')
  })

  it('emits markdown (not HTML) on every change', async () => {
    const onChange = vi.fn()
    render(<Harness initial="" onChange={onChange} />)

    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    await userEvent.click(region)
    await userEvent.keyboard('hello')

    await waitFor(() => expect(onChange).toHaveBeenCalled())
    expect(screen.getByTestId('markdown').textContent).toBe('hello')
    expect(onChange.mock.calls.at(-1)?.[0]).not.toContain('<p>')
  })

  it('applies bold from the toolbar and serialises it to markdown', async () => {
    render(<Harness initial="" />)
    const region = screen.getByRole('textbox', { name: /rich text editor/i })

    await userEvent.click(region)
    await userEvent.click(screen.getByRole('button', { name: 'Bold' }))
    await userEvent.keyboard('loud')

    await waitFor(() => expect(screen.getByTestId('markdown').textContent).toBe('**loud**'))
    expect(screen.getByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('turns a paragraph into a heading and a bullet list from the toolbar', async () => {
    render(<Harness initial="one" />)
    const region = screen.getByRole('textbox', { name: /rich text editor/i })

    await userEvent.click(region)
    await userEvent.click(screen.getByRole('button', { name: 'Heading 2' }))
    await waitFor(() => expect(screen.getByTestId('markdown').textContent).toBe('## one'))

    await userEvent.click(screen.getByRole('button', { name: 'Heading 2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Bullet list' }))
    await waitFor(() => expect(screen.getByTestId('markdown').textContent).toBe('- one'))
  })

  it('adds a link from the popover without using window.prompt', async () => {
    const promptSpy = vi.spyOn(window, 'prompt')
    render(<Harness initial="docs" />)

    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    await userEvent.click(region)
    await userEvent.keyboard('{Control>}a{/Control}')
    await userEvent.click(screen.getByRole('button', { name: 'Link' }))
    await userEvent.type(await screen.findByLabelText(/link url/i), 'https://stept.dev')
    await userEvent.click(screen.getByRole('button', { name: /apply link/i }))

    await waitFor(() =>
      expect(screen.getByTestId('markdown').textContent).toBe('[docs](https://stept.dev)')
    )
    expect(promptSpy).not.toHaveBeenCalled()
  })

  it('inserts an image from a pasted URL', async () => {
    render(<Harness initial="" />)
    await userEvent.click(screen.getByRole('textbox', { name: /rich text editor/i }))
    await userEvent.click(screen.getByRole('button', { name: /insert image/i }))
    await userEvent.type(await screen.findByLabelText(/image url/i), 'https://cdn.test/a.png')
    await userEvent.type(screen.getByLabelText(/alt text/i), 'Diagram')
    await userEvent.click(screen.getByRole('button', { name: /^insert$/i }))

    await waitFor(() =>
      expect(screen.getByTestId('markdown').textContent).toContain(
        '![Diagram](https://cdn.test/a.png)'
      )
    )
  })

  it('compact variant hides block-level toolbar controls', () => {
    render(<Harness variant="compact" />)
    expect(screen.getByRole('button', { name: 'Bold' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Italic' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inline code' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Link' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Heading 1' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Bullet list' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /insert image/i })).not.toBeInTheDocument()
  })

  it('re-renders the document when the parent replaces the markdown', async () => {
    function External() {
      const [value, setValue] = useState('first')
      return (
        <>
          <button onClick={() => setValue('## second')}>replace</button>
          <RichTextEditor value={value} onChange={setValue} />
        </>
      )
    }
    render(<External />)
    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    expect(region.textContent).toContain('first')

    await userEvent.click(screen.getByRole('button', { name: 'replace' }))
    await waitFor(() => expect(region.querySelector('h2')?.textContent).toBe('second'))
  })

  it('exposes the placeholder on the empty document and drops it once typed', async () => {
    render(
      <RichTextEditor value="" onChange={() => {}} placeholder="Write the release notes…" />
    )
    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    expect(region.querySelector('.is-editor-empty')).toHaveAttribute(
      'data-placeholder',
      'Write the release notes…'
    )

    await userEvent.click(region)
    await userEvent.keyboard('x')
    await waitFor(() => expect(region.querySelector('.is-editor-empty')).toBeNull())
  })

  it('disables editing and every toolbar control when disabled', () => {
    render(<RichTextEditor value="Locked" onChange={() => {}} disabled />)
    const region = screen.getByRole('textbox', { name: /rich text editor/i })
    expect(region).toHaveAttribute('contenteditable', 'false')
    expect(screen.getByRole('button', { name: 'Bold' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /insert image/i })).toBeDisabled()
  })

  it('keyboard shortcuts apply marks', async () => {
    render(<Harness initial="" />)
    await userEvent.click(screen.getByRole('textbox', { name: /rich text editor/i }))
    await userEvent.keyboard('{Control>}i{/Control}')
    await userEvent.keyboard('slant')

    await waitFor(() => expect(screen.getByTestId('markdown').textContent).toBe('*slant*'))
  })
})
