import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AiMenu } from './AiMenu'

const post = vi.hoisted(() => vi.fn())
const toastError = vi.hoisted(() => vi.fn())

vi.mock('@/api/client', () => ({
  api: { post },
  ws: (path: string) => `/api/v1/w/ws1${path}`,
}))
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError },
}))

/**
 * The editor's AI menu: which commands it offers for a selection versus an empty
 * cursor, what it sends, and where the result lands.
 */
function setup(
  props: Partial<React.ComponentProps<typeof AiMenu>> = {}
): { replace: ReturnType<typeof vi.fn>; insert: ReturnType<typeof vi.fn> } {
  const replace = vi.fn()
  const insert = vi.fn()
  render(
    <AiMenu
      disabled={false}
      selection=""
      document={'# Doc\n\nBody'}
      onReplaceSelection={replace}
      onInsert={insert}
      {...props}
    />
  )
  return { replace, insert }
}

beforeEach(() => {
  post.mockReset()
  toastError.mockReset()
  post.mockResolvedValue({ content: 'rewritten text', citations: [] })
})

describe('AiMenu with a selection', () => {
  it('offers rewrite commands and replaces the selection with the result', async () => {
    const { replace, insert } = setup({ selection: 'this are bad' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    await userEvent.click(screen.getByRole('button', { name: 'Improve writing' }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith('rewritten text'))
    expect(insert).not.toHaveBeenCalled()
    expect(post).toHaveBeenCalledWith('/api/v1/w/ws1/ai/write', {
      command: 'improve',
      context: 'this are bad',
      prompt: null,
      language: null,
    })
  })

  it('sends the target language for a translation', async () => {
    setup({ selection: 'Hello there' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    await userEvent.type(screen.getByLabelText('Translate into'), 'German')
    await userEvent.click(screen.getByRole('button', { name: 'Go' }))

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/v1/w/ws1/ai/write',
        expect.objectContaining({ command: 'translate', language: 'German' })
      )
    )
  })

  it('will not translate without a language', async () => {
    setup({ selection: 'Hello there' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled()
  })

  it('does not offer the draft prompt when text is selected', async () => {
    setup({ selection: 'some text' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    expect(screen.queryByLabelText('What should it write?')).not.toBeInTheDocument()
  })
})

describe('AiMenu with no selection', () => {
  it('drafts from a prompt and inserts at the cursor', async () => {
    post.mockResolvedValue({ content: '## Refunds', citations: [{ n: 1, title: 'Policy', url: null }] })
    const { replace, insert } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    await userEvent.type(screen.getByLabelText('What should it write?'), 'refund guide')
    await userEvent.click(screen.getByRole('button', { name: 'Draft' }))

    await waitFor(() => expect(insert).toHaveBeenCalledWith('## Refunds'))
    expect(replace).not.toHaveBeenCalled()
    // With nothing selected the whole document travels as context.
    expect(post).toHaveBeenCalledWith(
      '/api/v1/w/ws1/ai/write',
      expect.objectContaining({ command: 'draft', context: '# Doc\n\nBody', prompt: 'refund guide' })
    )
  })

  it('requires a prompt before drafting', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    expect(screen.getByRole('button', { name: 'Draft' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Outline' })).toBeDisabled()
  })
})

describe('AiMenu failures', () => {
  it('surfaces an API error and changes nothing', async () => {
    post.mockRejectedValue(new Error('provider unavailable'))
    const { replace, insert } = setup({ selection: 'text' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    await userEvent.click(screen.getByRole('button', { name: 'Improve writing' }))

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('provider unavailable'))
    expect(replace).not.toHaveBeenCalled()
    expect(insert).not.toHaveBeenCalled()
  })

  it('does not apply an empty result', async () => {
    post.mockResolvedValue({ content: '   ', citations: [] })
    const { replace } = setup({ selection: 'text' })
    await userEvent.click(screen.getByRole('button', { name: 'Ask AI' }))
    await userEvent.click(screen.getByRole('button', { name: 'Improve writing' }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(replace).not.toHaveBeenCalled()
  })

  it('is unavailable while the editor is read-only', () => {
    setup({ disabled: true })
    expect(screen.getByRole('button', { name: 'Ask AI' })).toBeDisabled()
  })
})
