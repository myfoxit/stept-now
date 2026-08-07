import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { McpSnippets, mcpOrigin, mcpSnippet } from './McpSnippets'

const URL = 'http://localhost:8600/mcp'

describe('mcpOrigin', () => {
  it('falls back to the backend dev origin, not the vite origin', () => {
    // No VITE_API_BASE_URL is set in tests and DEV is true — snippets must
    // point at the API origin (:8600), never the SPA origin.
    expect(mcpOrigin()).toBe('http://localhost:8600')
  })
})

describe('McpSnippets', () => {
  it('renders the exact Claude Code command with the raw key', () => {
    render(<McpSnippets endpointUrl={URL} rawKey="sk_stept_raw123" />)
    expect(
      screen.getByText(
        'claude mcp add --transport http stept http://localhost:8600/mcp --header "Authorization: Bearer sk_stept_raw123"'
      )
    ).toBeInTheDocument()
  })

  it('renders the exact curl command with the raw key', async () => {
    render(<McpSnippets endpointUrl={URL} rawKey="sk_stept_raw123" />)
    await userEvent.click(screen.getByRole('tab', { name: 'curl' }))
    expect(
      screen.getByText(
        `curl -X POST http://localhost:8600/mcp -H 'Authorization: Bearer sk_stept_raw123' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`
      )
    ).toBeInTheDocument()
  })

  it('uses the YOUR_STEPT_KEY placeholder when no raw key is provided', () => {
    render(<McpSnippets endpointUrl={URL} />)
    expect(
      screen.getByText(
        'claude mcp add --transport http stept http://localhost:8600/mcp --header "Authorization: Bearer YOUR_STEPT_KEY"'
      )
    ).toBeInTheDocument()
    expect(mcpSnippet('claude-desktop', URL)).toContain('"Authorization": "Bearer YOUR_STEPT_KEY"')
    expect(mcpSnippet('chatgpt', URL)).toBe(
      'URL: http://localhost:8600/mcp\nAuthorization: Bearer YOUR_STEPT_KEY'
    )
  })

  it('copies the active snippet and swaps the icon to a check', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const { container } = render(<McpSnippets endpointUrl={URL} rawKey="sk_stept_raw123" />)
    fireEvent.click(screen.getByRole('button', { name: 'Copy Claude Code snippet' }))

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        'claude mcp add --transport http stept http://localhost:8600/mcp --header "Authorization: Bearer sk_stept_raw123"'
      )
    )
    await waitFor(() => expect(container.querySelector('.lucide-check')).not.toBeNull())
  })
})
