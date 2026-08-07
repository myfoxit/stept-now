import { render } from 'preact'
import { afterEach, describe, expect, it } from 'vitest'

import type { ConversationSummary } from '../../types'
import { Home } from './Home'

let container: HTMLDivElement | null = null

afterEach(() => {
  if (container) {
    render(null, container)
    container.remove()
    container = null
  }
})

const conversations: ConversationSummary[] = [
  {
    id: 'c1',
    status: 'open',
    last_message_preview: 'Thanks for reaching out',
    last_activity_at: new Date().toISOString(),
    unread: true,
  },
]

describe('Home', () => {
  it('shows the greeting and recent conversations, and opens on click', () => {
    let opened = ''
    container = document.createElement('div')
    document.body.appendChild(container)
    render(
      <Home
        config={{ greeting: 'Welcome to Acme 👋' }}
        conversations={conversations}
        helpCenter
        onOpenConversation={(id) => (opened = id)}
        onNewConversation={() => {}}
        onOpenHelp={() => {}}
      />,
      container,
    )

    expect(container.textContent).toContain('Welcome to Acme 👋')
    expect(container.textContent).toContain('Thanks for reaching out')
    // Help CTA is present when helpCenter is enabled.
    expect(container.textContent).toContain('Search for help')

    const row = container.querySelector('.sw-conv-row') as HTMLButtonElement
    expect(row).not.toBeNull()
    row.click()
    expect(opened).toBe('c1')
  })
})
