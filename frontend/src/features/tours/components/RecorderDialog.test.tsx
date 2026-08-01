import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { resetAuth, seedAuth } from '../test-utils'
import { RecorderDialog } from './RecorderDialog'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const TOKEN_ROUTE = 'POST /api/v1/w/w1/tours/recorder-token'
const RELEASE_ROUTE = 'GET /extension-assets/release.json'

const release = {
  available: true,
  version: '0.2.0',
  size_bytes: 135509,
  sha256: 'abc123',
  download_url: 'http://localhost:8600/extension-assets/stept-recorder.zip',
  api_base: 'http://localhost:8600',
  web_store_url: null,
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('RecorderDialog', () => {
  it('offers a direct download and the server address to pair against', async () => {
    mockFetch({
      [RELEASE_ROUTE]: () => ({ body: release }),
      [TOKEN_ROUTE]: () => ({ body: { token: 'tok_abc', expires_days: 7 } }),
    })
    renderApp(<RecorderDialog open onOpenChange={() => {}} />)

    const download = await screen.findByRole('link', { name: /download the extension/i })
    expect(download).toHaveAttribute('href', release.download_url)
    expect(download).toHaveAttribute('download')
    expect(screen.getByText(/v0\.2\.0/)).toBeInTheDocument()

    // A self-hoster's backend is not the extension's compiled-in default, so
    // the address it must be pointed at has to be shown.
    expect(screen.getByTestId('recorder-api-base')).toHaveTextContent('http://localhost:8600')
  })

  it('links to the web store instead of the zip once a listing is configured', async () => {
    mockFetch({
      [RELEASE_ROUTE]: () => ({
        body: { ...release, web_store_url: 'https://chromewebstore.google.com/detail/xyz' },
      }),
      [TOKEN_ROUTE]: () => ({ body: { token: 'tok_abc', expires_days: 7 } }),
    })
    renderApp(<RecorderDialog open onOpenChange={() => {}} />)

    const link = await screen.findByRole('link', { name: /add to chrome/i })
    expect(link).toHaveAttribute('href', 'https://chromewebstore.google.com/detail/xyz')
    expect(screen.queryByRole('link', { name: /download the extension/i })).not.toBeInTheDocument()
  })

  it('explains how to produce a build when the server has none', async () => {
    mockFetch({
      [RELEASE_ROUTE]: () => ({
        body: { available: false, api_base: 'http://localhost:8600', web_store_url: null },
      }),
      [TOKEN_ROUTE]: () => ({ body: { token: 'tok_abc', expires_days: 7 } }),
    })
    renderApp(<RecorderDialog open onOpenChange={() => {}} />)

    expect(await screen.findByText(/no build found on the server/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /download the extension/i })).not.toBeInTheDocument()
  })

  it('keeps the paste-in token as a fallback and copies it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    mockFetch({
      [RELEASE_ROUTE]: () => ({ body: release }),
      [TOKEN_ROUTE]: () => ({ body: { token: 'tok_abc', expires_days: 7 } }),
    })
    renderApp(<RecorderDialog open onOpenChange={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('recorder-token')).toHaveTextContent('tok_abc'))
    await userEvent.click(screen.getByRole('button', { name: 'Copy token' }))
    expect(writeText).toHaveBeenCalledWith('tok_abc')
  })
})
