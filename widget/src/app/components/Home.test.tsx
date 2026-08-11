import { render, type ComponentChild } from 'preact'
import { afterEach, describe, expect, it } from 'vitest'

import type { ConversationSummary } from '../../types'
import type { FederatedResults } from '../api-extra'
import { Home, rowPreview } from './Home'

let containers: HTMLDivElement[] = []

afterEach(() => {
  for (const container of containers) {
    render(null, container)
    container.remove()
  }
  containers = []
})

function mount(node: ComponentChild): HTMLDivElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  render(node, container)
  containers.push(container)
  return container
}

const conversations: ConversationSummary[] = [
  {
    id: 'c1',
    status: 'open',
    last_message_preview: 'Thanks for reaching out',
    last_activity_at: new Date().toISOString(),
    unread: true,
  },
]

const noop = (): void => {}

function homeProps() {
  return {
    config: {},
    conversations: [] as ConversationSummary[],
    helpCenter: false,
    search: null as FederatedResults | null,
    onSearch: noop,
    onOpenConversation: noop,
    onNewConversation: noop,
    onOpenHelp: noop,
    onOpenArticle: noop,
    onStartTour: noop,
  }
}

describe('Home', () => {
  it('shows the greeting and recent conversations, and opens on click', () => {
    let opened = ''
    const el = mount(
      <Home
        {...homeProps()}
        config={{ greeting: 'Welcome to Acme 👋' }}
        conversations={conversations}
        helpCenter
        onOpenConversation={(id) => (opened = id)}
      />,
    )

    expect(el.textContent).toContain('Welcome to Acme 👋')
    expect(el.textContent).toContain('Thanks for reaching out')
    // Help CTA is present when helpCenter is enabled.
    expect(el.textContent).toContain('Search for help')

    const row = el.querySelector('.sw-conv-row') as HTMLButtonElement
    expect(row).not.toBeNull()
    row.click()
    expect(opened).toBe('c1')
  })

  it('marks "your turn" on unread conversations whose agent asked a question', () => {
    const asked: ConversationSummary = {
      id: 'c2',
      status: 'open',
      last_message_preview: 'Which plan are you on?',
      last_activity_at: new Date().toISOString(),
      unread: true,
    }
    const el = mount(<Home {...homeProps()} conversations={[asked]} />)
    expect(el.querySelector('.sw-your-turn')).not.toBeNull()
    expect(el.querySelector('.sw-unread-dot')).not.toBeNull()

    const read = mount(<Home {...homeProps()} conversations={[{ ...asked, unread: false }]} />)
    expect(read.querySelector('.sw-your-turn')).toBeNull()
  })

  it('makes the single AI promise, not the "we’ll get back to you" one', () => {
    const el = mount(<Home {...homeProps()} config={{ ai_agent_id: 'agent1' }} />)
    expect(el.textContent).toContain('Ask anything — our AI answers right away.')
  })
})

describe('rowPreview', () => {
  const base: ConversationSummary = {
    id: 'c1',
    status: 'open',
    last_message_preview: null,
    last_activity_at: new Date().toISOString(),
    unread: false,
  }

  it('prefers the backend-generated subject over the raw last message', () => {
    const c = {
      ...base,
      subject: 'Widget setup on doktrace',
      last_message_preview: 'Sorry, I could not find anything about that in the docs…',
    } as ConversationSummary
    expect(rowPreview(c)).toBe('Widget setup on doktrace')
  })

  it('trims the last message at a word boundary, never mid-word', () => {
    const c = {
      ...base,
      last_message_preview:
        'Die Einrichtung deiner Wissensdatenbank beginnt mit dem ersten Dokument im Portal',
    }
    const preview = rowPreview(c)
    expect(preview.length).toBeLessThanOrEqual(61)
    expect(preview.endsWith('…')).toBe(true)
    // Word-boundary cut: the fragment before the ellipsis is a whole word.
    expect(preview).toBe('Die Einrichtung deiner Wissensdatenbank beginnt mit dem…')
  })

  it('falls back to a generic label when there is nothing to show', () => {
    expect(rowPreview(base)).toBe('Conversation')
  })
})

describe('Home federated search', () => {
  const results: FederatedResults = {
    query: 'setup',
    loading: false,
    articles: [{ title: 'Set up your workspace', slug: 'setup', snippet: 'How to begin' }],
    tours: [{ id: 'tour1', name: 'Setup walkthrough', steps: 5 }],
  }

  it('renders grouped Articles and Tours results', () => {
    const el = mount(<Home {...homeProps()} search={results} />)
    expect(el.textContent).toContain('Articles')
    expect(el.textContent).toContain('Tours')
    expect(el.textContent).toContain('Set up your workspace')
    expect(el.textContent).toContain('Setup walkthrough')
    // Search mode replaces the cards/rows — no duplicated Home furniture.
    expect(el.querySelector('.sw-card')).toBeNull()
  })

  it('opens an article and starts a tour from the results', () => {
    let openedSlug = ''
    let startedTour = ''
    const el = mount(
      <Home
        {...homeProps()}
        search={results}
        onOpenArticle={(slug) => (openedSlug = slug)}
        onStartTour={(id) => (startedTour = id)}
      />,
    )
    ;(el.querySelector('.sw-article-row') as HTMLButtonElement).click()
    expect(openedSlug).toBe('setup')
    ;(el.querySelector('.sw-tour-row-start') as HTMLButtonElement).click()
    expect(startedTour).toBe('tour1')
  })

  it('shows an empty state for a query with no matches', () => {
    const el = mount(
      <Home
        {...homeProps()}
        search={{ query: 'zzz', loading: false, articles: [], tours: [] }}
      />,
    )
    expect(el.textContent).toContain('No results for “zzz”.')
  })
})
