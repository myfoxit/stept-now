import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { AddSourceDialog } from './AddSourceDialog'
import { makeDocument, makeSource, seedAuth } from '../test-utils'

describe('AddSourceDialog', () => {
  beforeEach(() => seedAuth())

  it('uploads files by creating a source then posting each document', async () => {
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src1', type: 'files' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src1/documents': () => ({
        status: 201,
        body: makeDocument({ id: 'd1' }),
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    const input = screen.getByLabelText(/upload files/i)
    await userEvent.upload(input, new File(['hello'], 'guide.txt', { type: 'text/plain' }))
    expect(await screen.findByText('guide.txt')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const calls = fetchFn.mock.calls.map(([url, init]) => `${init?.method} ${url}`)
      expect(calls.some((c) => c === 'POST /api/v1/w/w1/knowledge/sources')).toBe(true)
      expect(
        calls.some((c) => c === 'POST /api/v1/w/w1/knowledge/sources/src1/documents')
      ).toBe(true)
    })
  })

  it('creates a text source with a pasted document', async () => {
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src2', type: 'text' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src2/documents': () => ({
        status: 201,
        body: makeDocument({ id: 'd2' }),
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /text/i }))
    await userEvent.type(screen.getByLabelText(/document title/i), 'Refund policy')
    await userEvent.type(screen.getByLabelText(/content/i), 'We offer refunds within 30 days.')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const textCall = fetchFn.mock.calls.find(
        ([url, init]) =>
          init?.method === 'POST' && String(url).endsWith('/sources/src2/documents')
      )
      expect(textCall).toBeDefined()
      expect(JSON.parse(String(textCall![1]?.body))).toMatchObject({ title: 'Refund policy' })
    })
  })
})
