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

  it('uploads files by creating a source then posting one batch', async () => {
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src1', type: 'files' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src1/documents/batch': () => ({
        status: 201,
        body: [makeDocument({ id: 'd1' }), makeDocument({ id: 'd2' })],
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    const input = screen.getByLabelText(/upload files/i)
    await userEvent.upload(input, [
      new File(['hello'], 'guide.txt', { type: 'text/plain' }),
      new File(['more'], 'faq.md', { type: 'text/markdown' }),
    ])
    expect(await screen.findByText('guide.txt')).toBeInTheDocument()
    expect(screen.getByText('faq.md')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const calls = fetchFn.mock.calls.map(([url, init]) => `${init?.method} ${url}`)
      expect(calls).toContain('POST /api/v1/w/w1/knowledge/sources')
      expect(calls).toContain('POST /api/v1/w/w1/knowledge/sources/src1/documents/batch')
    })
    const batch = fetchFn.mock.calls.find(([url]) => String(url).endsWith('/documents/batch'))!
    const form = batch[1]?.body as FormData
    expect(form.getAll('file')).toHaveLength(2)
  })

  it('creates a text source authored in the rich text editor', async () => {
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

    const editor = screen.getByRole('textbox', { name: /document content/i })
    await userEvent.click(editor)
    await userEvent.click(screen.getByRole('button', { name: 'Heading 2' }))
    await userEvent.keyboard('Refunds')

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create source/i })).toBeEnabled()
    )
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const textCall = fetchFn.mock.calls.find(
        ([url, init]) =>
          init?.method === 'POST' && String(url).endsWith('/sources/src2/documents')
      )
      expect(textCall).toBeDefined()
      // The editor serialises back to markdown — never HTML.
      expect(JSON.parse(String(textCall![1]?.body))).toEqual({
        title: 'Refund policy',
        content: '## Refunds',
      })
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
        config: {
          base_url: 'https://docs.example.com',
          max_pages: 50,
          max_depth: 3,
          // Politeness defaults ship even when the user touches nothing.
          respect_robots: true,
          delay_ms: 250,
          include_patterns: [],
          exclude_patterns: [],
        },
      })
    })
  })

  it('serializes crawl include/exclude chips, robots switch and delay into config', async () => {
    const fetchFn = mockCreateRoutes('src4b')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /crawl/i }))
    await userEvent.type(screen.getByLabelText(/base url/i), 'https://docs.example.com')

    const include = screen.getByLabelText(/include patterns/i)
    await userEvent.type(include, '/docs/*{Enter}')
    await userEvent.type(include, '/guides/*{Enter}')
    // Duplicates are ignored rather than added twice.
    await userEvent.type(include, '/docs/*{Enter}')
    await userEvent.type(screen.getByLabelText(/exclude patterns/i), '/blog/*{Enter}')

    await userEvent.click(screen.getByLabelText(/respect robots/i))
    const delay = screen.getByLabelText(/delay between requests/i)
    await userEvent.clear(delay)
    await userEvent.type(delay, '1000')

    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'crawl',
        config: {
          include_patterns: ['/docs/*', '/guides/*'],
          exclude_patterns: ['/blog/*'],
          respect_robots: false,
          delay_ms: 1000,
        },
      })
    })
  })

  it('removes a crawl pattern chip and rejects an out-of-range delay', async () => {
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /crawl/i }))
    await userEvent.type(screen.getByLabelText(/base url/i), 'https://docs.example.com')
    await userEvent.type(screen.getByLabelText(/include patterns/i), '/docs/*{Enter}')
    expect(screen.getByText('/docs/*')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove /docs/*' }))
    expect(screen.queryByText('/docs/*')).not.toBeInTheDocument()

    const delay = screen.getByLabelText(/delay between requests/i)
    await userEvent.clear(delay)
    await userEvent.type(delay, '5000')
    expect(screen.getByRole('button', { name: /create source/i })).toBeDisabled()
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

  it('creates a confluence source in token mode with space keys parsed into config', async () => {
    const fetchFn = mockCreateRoutes('src9')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /confluence/i }))
    // Token mode is the default — no integrations round trip needed.
    expect(screen.getByLabelText('Authentication')).toHaveValue('token')
    await userEvent.type(
      screen.getByLabelText(/site url/i),
      'https://acme.atlassian.net'
    )
    await userEvent.type(screen.getByLabelText(/account email/i), 'me@acme.com')
    await userEvent.type(screen.getByLabelText(/^api token/i), 'atl-token')
    await userEvent.type(screen.getByLabelText(/space keys/i), 'DOCS, HELP')
    await userEvent.type(screen.getByLabelText(/max pages/i), '200')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'confluence',
        config: {
          auth: 'token',
          base_url: 'https://acme.atlassian.net',
          email: 'me@acme.com',
          space_keys: ['DOCS', 'HELP'],
          max_pages: 200,
        },
        secrets: { api_token: 'atl-token' },
      })
    })
  })

  it('confluence OAuth mode sends connection_id and no secrets', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            {
              id: 'confluence',
              name: 'Confluence',
              category: 'knowledge',
              auth: 'oauth2',
              description: '',
              doc_slug: 'confluence',
              configured: true,
              connections: [
                {
                  id: 'conn-c1',
                  provider: 'confluence',
                  status: 'connected',
                  account_label: 'acme.atlassian.net',
                  scopes: [],
                  meta: {},
                  created_at: '2026-08-01T00:00:00Z',
                },
              ],
              credential: null,
            },
          ],
        },
      }),
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src10', type: 'confluence' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src10/sync': () => ({
        body: makeSource({ id: 'src10', status: 'syncing' }),
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /confluence/i }))
    await userEvent.selectOptions(screen.getByLabelText('Authentication'), 'oauth')
    // Wait for the integrations query to populate the select before choosing.
    const option = await screen.findByRole('option', { name: 'acme.atlassian.net' })
    await userEvent.selectOptions(screen.getByLabelText('Atlassian account'), option)
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'POST', '/knowledge/sources')
      expect(body).toMatchObject({
        type: 'confluence',
        config: { auth: 'oauth', connection_id: 'conn-c1' },
      })
      expect(body).not.toHaveProperty('secrets')
    })
  })

  it('gdrive requires a Google connection + folder ids and serializes them', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            {
              id: 'google',
              name: 'Google (Gmail & Drive)',
              category: 'email',
              auth: 'oauth2',
              description: '',
              doc_slug: 'google',
              configured: true,
              connections: [
                {
                  id: 'conn-g1',
                  provider: 'google',
                  status: 'connected',
                  account_label: 'me@acme.com',
                  scopes: [],
                  meta: {},
                  created_at: '2026-08-01T00:00:00Z',
                },
              ],
              credential: null,
            },
          ],
        },
      }),
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src11', type: 'gdrive' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src11/sync': () => ({
        body: makeSource({ id: 'src11', status: 'syncing' }),
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /drive/i }))
    const googleOption = await screen.findByRole('option', { name: 'me@acme.com' })
    await userEvent.selectOptions(screen.getByLabelText('Google account'), googleOption)
    expect(screen.getByRole('button', { name: /create source/i })).toBeDisabled()

    await userEvent.type(
      screen.getByLabelText(/folder ids/i),
      'folder-one\nfolder-two'
    )
    await userEvent.type(screen.getByLabelText(/max files/i), '100')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'gdrive',
        config: {
          connection_id: 'conn-g1',
          folder_ids: ['folder-one', 'folder-two'],
          max_files: 100,
        },
      })
    })
  })

  it('shows a Connect deep link instead of the gdrive select when no Google connection exists', async () => {
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: { providers: [] } }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /drive/i }))
    const link = await screen.findByRole('link', { name: /connect google/i })
    expect(link).toHaveAttribute('href', '/settings/integrations')
  })

  it('zendesk sends subdomain/locale as config and email + api token as secrets', async () => {
    const fetchFn = mockCreateRoutes('src12')
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /zendesk/i }))
    await userEvent.type(screen.getByLabelText(/subdomain/i), 'acme')
    expect(screen.getByLabelText('Locale')).toHaveValue('en-us')
    await userEvent.type(screen.getByLabelText(/account email/i), 'me@acme.com')
    const token = screen.getByLabelText(/^api token/i)
    expect(token).toHaveAttribute('type', 'password')
    await userEvent.type(token, 'zd-token')
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      expect(bodyOf(fetchFn, 'POST', '/knowledge/sources')).toMatchObject({
        type: 'zendesk',
        config: { subdomain: 'acme', locale: 'en-us' },
        secrets: { email: 'me@acme.com', api_token: 'zd-token' },
      })
    })
  })

  it('notion OAuth mode swaps the token field for a connection select', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            {
              id: 'notion',
              name: 'Notion',
              category: 'knowledge',
              auth: 'oauth2',
              description: '',
              doc_slug: 'notion',
              configured: true,
              connections: [
                {
                  id: 'conn-n1',
                  provider: 'notion',
                  status: 'connected',
                  account_label: 'Acme HQ',
                  scopes: [],
                  meta: {},
                  created_at: '2026-08-01T00:00:00Z',
                },
              ],
              credential: null,
            },
          ],
        },
      }),
      'POST /api/v1/w/w1/knowledge/sources': () => ({
        status: 201,
        body: makeSource({ id: 'src13', type: 'notion' }),
      }),
      'POST /api/v1/w/w1/knowledge/sources/src13/sync': () => ({
        body: makeSource({ id: 'src13', status: 'syncing' }),
      }),
    })
    renderApp(<AddSourceDialog open onOpenChange={() => {}} />)

    await userEvent.click(screen.getByRole('tab', { name: /notion/i }))
    // Token mode requires a token…
    expect(screen.getByRole('button', { name: /create source/i })).toBeDisabled()

    // …OAuth mode requires a connection instead.
    await userEvent.selectOptions(screen.getByLabelText('Authentication'), 'oauth')
    expect(screen.queryByLabelText(/integration token/i)).not.toBeInTheDocument()
    const notionOption = await screen.findByRole('option', { name: 'Acme HQ' })
    await userEvent.selectOptions(screen.getByLabelText('Notion account'), notionOption)
    await userEvent.click(screen.getByRole('button', { name: /create source/i }))

    await waitFor(() => {
      const body = bodyOf(fetchFn, 'POST', '/knowledge/sources')
      expect(body).toMatchObject({
        type: 'notion',
        config: { auth: 'oauth', connection_id: 'conn-n1' },
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
