import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { AddSourceDialog } from './AddSourceDialog'
import type { Source } from '../api'
import { makeDocument, makeSource, seedAuth } from '../test-utils'

/** The parsed JSON body of the first call matching `method` + URL suffix. */
function bodyOf(
  fetchFn: ReturnType<typeof mockFetch>,
  method: string,
  suffix: string
): Record<string, unknown> {
  const call = fetchFn.mock.calls.find(
    ([url, init]) => init?.method === method && String(url).split('?')[0]!.endsWith(suffix)
  )
  expect(call, `${method} ${suffix} was not called`).toBeDefined()
  return JSON.parse(String(call![1]?.body))
}

function mockCreateRoutes(id: string) {
  return mockFetch({
    'POST /api/v1/w/w1/knowledge/sources': () => ({
      status: 201,
      body: makeSource({ id }),
    }),
    [`POST /api/v1/w/w1/knowledge/sources/${id}/sync`]: () => ({
      body: makeSource({ id, status: 'syncing' }),
    }),
  })
}

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

  it('creates a sitemap source with refresh_minutes serialized into config', async () => {
    const fetchFn = mockCreateRoutes('src3')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /sitemap/i }))
    await userEvent.type(
      screen.getByLabelText(/sitemap url/i),
      'https://example.com/sitemap.xml'
    )
    await userEvent.type(screen.getByLabelText(/max pages/i), '200')
    await userEvent.type(screen.getByLabelText(/auto re-sync/i), '30')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'POST', '/knowledge/sources')
      expect(body).toMatchObject({
        type: 'sitemap',
        config: {
          sitemap_url: 'https://example.com/sitemap.xml',
          max_pages: 200,
          refresh_minutes: 30,
        },
      })
      expect(body).not.toHaveProperty('secrets')
    })
  })

  it('creates a crawl source with base_url, max_pages and max_depth', async () => {
    const fetchFn = mockCreateRoutes('src4')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /crawl/i }))
    await userEvent.type(screen.getByLabelText(/base url/i), 'https://docs.example.com')
    await userEvent.type(screen.getByLabelText(/max pages/i), '50')
    await userEvent.type(screen.getByLabelText(/max depth/i), '3')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'crawl',
        config: { base_url: 'https://docs.example.com', max_pages: 50, max_depth: 3 },
      })
    })
  })

  it('creates a github source and sends the token as a secret only when typed', async () => {
    const fetchFn = mockCreateRoutes('src5')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /github/i }))
    await userEvent.type(screen.getByLabelText(/repository owner/i), 'acme')
    await userEvent.type(screen.getByLabelText(/^repository$/i), 'docs')
    await userEvent.type(screen.getByLabelText(/branch/i), 'main')
    await userEvent.click(screen.getByLabelText(/issues/i))
    await userEvent.type(screen.getByLabelText(/access token/i), 'ghp_secret')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'POST', '/knowledge/sources')
      expect(body).toMatchObject({
        type: 'github',
        config: {
          repo_owner: 'acme',
          repo: 'docs',
          branch: 'main',
          include_files: true,
          include_issues: true,
          include_prs: false,
        },
        secrets: { token: 'ghp_secret' },
      })
    })
  })

  it('omits secrets entirely for a github source without a token', async () => {
    const fetchFn = mockCreateRoutes('src6')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /github/i }))
    await userEvent.type(screen.getByLabelText(/repository owner/i), 'acme')
    await userEvent.type(screen.getByLabelText(/^repository$/i), 'docs')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'POST', '/knowledge/sources')
      expect(body).toMatchObject({ type: 'github' })
      expect(body).not.toHaveProperty('secrets')
    })
  })

  it('requires a notion token before allowing submit, then sends it as a secret', async () => {
    const fetchFn = mockCreateRoutes('src7')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /notion/i }))
    expect(screen.getByRole('button', { name: /create source/i })).toBeDisabled()

    const tokenInput = screen.getByLabelText(/integration token/i)
    expect(tokenInput).toHaveAttribute('type', 'password')
    await userEvent.type(tokenInput, 'secret_ntn')
    await userEvent.type(screen.getByLabelText(/root page id/i), 'abc123')
    expect(screen.getByRole('button', { name: /create source/i })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'notion',
        config: { root_page_id: 'abc123' },
        secrets: { token: 'secret_ntn' },
      })
    })
  })

  it('edits a source with stored credentials: blank "unchanged" secret stays untouched', async () => {
    const source = makeSource({
      id: 'src8',
      type: 'github',
      name: 'Docs repo',
      config: { repo_owner: 'acme', repo: 'docs', include_files: true, refresh_minutes: 15 },
      has_secrets: true,
    }) as unknown as Source
    const fetchFn = mockFetch({
      'PATCH /api/v1/w/w1/knowledge/sources/src8': () => ({ body: source }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} source={source} />)

    const tokenInput = screen.getByLabelText(/access token/i)
    expect(tokenInput).toHaveValue('')
    expect(tokenInput).toHaveAttribute('placeholder', expect.stringContaining('unchanged'))

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'PATCH', '/knowledge/sources/src8')
      expect(body).toMatchObject({
        name: 'Docs repo',
        config: { repo_owner: 'acme', repo: 'docs', refresh_minutes: 15 },
      })
      expect(body).not.toHaveProperty('secrets')
    })
  })

  it('shows validation errors from an ApiError response inline', async () => {
    mockFetch({
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 422,
        body: {
          error: {
            code: 'validation_failure',
            message: 'Invalid source config',
            details: [{ loc: ['config', 'sitemap_url'], msg: 'must be a valid URL' }],
          },
        },
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /sitemap/i }))
    await userEvent.type(screen.getByLabelText(/sitemap url/i), 'not-a-url')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    expect(await screen.findByText('Invalid source config')).toBeInTheDocument()
    expect(screen.getByText(/must be a valid URL/i)).toBeInTheDocument()
  })
})
