import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { AddDocumentDialog } from './AddDocumentDialog'
import type { Source } from '../api'
import { makeDocument, makeSource, seedAuth } from '../test-utils'

const filesSource = makeSource({ id: 'src1', type: 'files' }) as unknown as Source
const textSource = makeSource({ id: 'src2', type: 'text', name: 'Handbook' }) as unknown as Source

/** The <li> for a file, so status badges can be asserted per row. */
function fileRow(name: string) {
  return screen.getByText(name).closest('li') as HTMLElement
}

describe('AddDocumentDialog', () => {
  beforeEach(() => seedAuth())

  it('uploads several files in one batch request and closes on success', async () => {
    const onOpenChange = vi.fn()
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/knowledge/sources/src1/documents/batch': () => ({
        status: 201,
        body: [
          makeDocument({ id: 'd1', title: 'guide.txt' }),
          makeDocument({ id: 'd2', title: 'faq.md' }),
        ],
      }),
    })
    renderApp(<AddDocumentDialog source={filesSource} open onOpenChange={onOpenChange} />)

    await userEvent.upload(screen.getByLabelText(/upload files/i), [
      new File(['a'], 'guide.txt', { type: 'text/plain' }),
      new File(['b'], 'faq.md', { type: 'text/markdown' }),
    ])
    expect(screen.getByText('guide.txt')).toBeInTheDocument()
    expect(screen.getByText('faq.md')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^upload$/i }))

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
    const calls = fetchFn.mock.calls.filter(([url]) => String(url).endsWith('/documents/batch'))
    expect(calls).toHaveLength(1)
    expect((calls[0][1]?.body as FormData).getAll('file')).toHaveLength(2)
  })

  it('shows the per-file error when one file in the batch fails', async () => {
    mockFetch({
      'POST /api/v1/w/w1/knowledge/sources/src1/documents/batch': () => ({
        status: 201,
        body: [
          makeDocument({ id: 'd1', title: 'guide.txt' }),
          makeDocument({
            id: 'd2',
            title: 'broken.pdf',
            status: 'failed',
            error: 'Could not extract text',
          }),
        ],
      }),
    })
    renderApp(<AddDocumentDialog source={filesSource} open onOpenChange={() => {}} />)

    await userEvent.upload(screen.getByLabelText(/upload files/i), [
      new File(['a'], 'guide.txt', { type: 'text/plain' }),
      new File(['b'], 'broken.pdf', { type: 'application/pdf' }),
    ])
    await userEvent.click(screen.getByRole('button', { name: /^upload$/i }))

    await waitFor(() => {
      expect(within(fileRow('guide.txt')).getByText('Indexed')).toBeInTheDocument()
      expect(within(fileRow('broken.pdf')).getByText('Failed')).toBeInTheDocument()
    })
    expect(screen.getByText('Could not extract text')).toBeInTheDocument()
  })

  it('marks every file failed when the whole request is rejected', async () => {
    mockFetch({
      'POST /api/v1/w/w1/knowledge/sources/src1/documents/batch': () => ({
        status: 400,
        body: { error: { code: 'bad_request', message: 'Too many files' } },
      }),
    })
    renderApp(<AddDocumentDialog source={filesSource} open onOpenChange={() => {}} />)

    await userEvent.upload(
      screen.getByLabelText(/upload files/i),
      new File(['a'], 'guide.txt', { type: 'text/plain' })
    )
    await userEvent.click(screen.getByRole('button', { name: /^upload$/i }))

    await waitFor(() => expect(screen.getByText('Failed')).toBeInTheDocument())
    expect(screen.getByText('Too many files')).toBeInTheDocument()
  })

  it('requires at least one file before uploading', () => {
    renderApp(<AddDocumentDialog source={filesSource} open onOpenChange={() => {}} />)
    expect(screen.getByRole('button', { name: /^upload$/i })).toBeDisabled()
  })

  it('authors a text document through the rich text editor and posts markdown', async () => {
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/knowledge/sources/src2/documents': () => ({
        status: 201,
        body: makeDocument({ id: 'd3' }),
      }),
    })
    renderApp(<AddDocumentDialog source={textSource} open onOpenChange={() => {}} />)

    await userEvent.type(screen.getByLabelText(/^title$/i), 'Refunds')
    const editor = screen.getByRole('textbox', { name: /document content/i })
    await userEvent.click(editor)
    await userEvent.click(screen.getByRole('button', { name: 'Bullet list' }))
    await userEvent.keyboard('Within 30 days')

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /add document/i })).toBeEnabled()
    )
    await userEvent.click(screen.getByRole('button', { name: /add document/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(([url]) => String(url).endsWith('/sources/src2/documents'))
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1]?.body))).toEqual({
        title: 'Refunds',
        content: '- Within 30 days',
      })
    })
  })
})
