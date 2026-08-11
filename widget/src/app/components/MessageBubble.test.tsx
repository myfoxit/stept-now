import { render } from 'preact'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FeedbackRating } from '../../types'
import type { UiMessage } from '../controller'
import { feedbackStorageKey, MessageBubble } from './MessageBubble'

let containers: HTMLDivElement[] = []

function mount(node: ReturnType<typeof MessageBubble>): HTMLDivElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  render(node, container)
  containers.push(container)
  return container
}

/** Flush Preact's microtask-scheduled re-render after events. */
const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

/** Working localStorage mock (Node's built-in global stub is nonfunctional). */
function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
})

afterEach(() => {
  for (const container of containers) {
    render(null, container)
    container.remove()
  }
  containers = []
  vi.unstubAllGlobals()
})

const base: UiMessage = {
  id: 'm1',
  direction: 'in',
  author_type: 'contact',
  author_name: 'Jane',
  content: 'Hello there',
  attachments: [],
  created_at: new Date().toISOString(),
  meta: {},
}

const agentMsg: UiMessage = {
  ...base,
  id: 'ai1',
  direction: 'out',
  author_type: 'agent',
  author_name: 'Sage',
  content: 'Here is the answer.',
}

describe('MessageBubble', () => {
  it('aligns the visitor message to the right and renders content', () => {
    const el = mount(<MessageBubble message={base} />)
    expect(el.querySelector('.sw-row-mine')).not.toBeNull()
    expect(el.querySelector('.sw-row-them')).toBeNull()
    expect(el.textContent).toContain('Hello there')
  })

  it('renders an agent reply on the left with citation links', () => {
    const msg: UiMessage = {
      ...base,
      id: 'm2',
      direction: 'out',
      author_type: 'agent',
      author_name: 'Sage',
      content: 'See docs [1]',
      meta: { citations: [{ n: 1, title: 'Install guide', url: 'https://stept.io/install' }] },
    }
    const el = mount(<MessageBubble message={msg} />)
    expect(el.querySelector('.sw-row-them')).not.toBeNull()
    const cite = el.querySelector('a.sw-cite') as HTMLAnchorElement | null
    expect(cite).not.toBeNull()
    expect(cite!.getAttribute('href')).toBe('https://stept.io/install')
    expect(cite!.textContent).toContain('Install guide')
  })
})

describe('MessageBubble agent identity', () => {
  it('renders the agent name with the AI disclosure chip', () => {
    const el = mount(<MessageBubble message={agentMsg} />)
    const author = el.querySelector('.sw-author')
    expect(author).not.toBeNull()
    expect(author!.textContent).toContain('Sage')
    expect(el.querySelector('.sw-ai-chip')!.textContent).toBe('AI')
  })

  it('falls back to the configured persona when the message has no author', () => {
    const anon: UiMessage = { ...agentMsg, id: 'ai2', author_name: '' }
    const el = mount(<MessageBubble message={anon} agentName="Northplane Guide" />)
    expect(el.querySelector('.sw-author')!.textContent).toContain('Northplane Guide')
    // The avatar initials follow the same resolved name.
    expect(el.querySelector('.sw-avatar')!.textContent).toBe('NG')
  })

  it('omits the AI chip when the workspace disabled disclosure, and for humans', () => {
    const noChip = mount(<MessageBubble message={agentMsg} aiDisclosure={false} />)
    expect(noChip.querySelector('.sw-ai-chip')).toBeNull()

    const human: UiMessage = { ...agentMsg, id: 'h2', author_type: 'user', author_name: 'Alex' }
    const humanEl = mount(<MessageBubble message={human} />)
    expect(humanEl.querySelector('.sw-author')!.textContent).toContain('Alex')
    expect(humanEl.querySelector('.sw-ai-chip')).toBeNull()
  })

  it('never labels the visitor’s own messages', () => {
    const el = mount(<MessageBubble message={base} />)
    expect(el.querySelector('.sw-author')).toBeNull()
  })
})

describe('MessageBubble tour attachments', () => {
  it('renders a tour_offer as a card and starts the tour', () => {
    const offerMsg: UiMessage = {
      ...agentMsg,
      id: 'offer1',
      content: 'I can walk you through it.',
      attachments: [
        { kind: 'tour_offer', tour_id: 't9', title: 'Widget setup', steps: 5, est_seconds: 120 },
      ],
    }
    const started: string[] = []
    const el = mount(
      <MessageBubble message={offerMsg} onStartTour={(id) => started.push(id)} />,
    )
    expect(el.textContent).toContain('Widget setup')
    expect(el.textContent).toContain('5 steps')
    expect(el.textContent).toContain('~2 min')
    ;(el.querySelector('.sw-tour-card-start') as HTMLButtonElement).click()
    expect(started).toEqual(['t9'])
  })

  it('renders a tour_event as a centered system line, not a bubble', () => {
    const eventMsg: UiMessage = {
      ...agentMsg,
      id: 'ev1',
      content: 'tour dismissed',
      attachments: [{ kind: 'tour_event', event: 'dismissed', step: 2 }],
    }
    const el = mount(<MessageBubble message={eventMsg} />)
    expect(el.querySelector('.sw-bubble')).toBeNull()
    expect(el.querySelector('.sw-activity')!.textContent).toBe('✕ Dismissed at step 2')

    const started: UiMessage = {
      ...agentMsg,
      id: 'ev2',
      content: '',
      attachments: [{ kind: 'tour_event', event: 'started', title: 'Widget setup' }],
    }
    const startedEl = mount(<MessageBubble message={started} />)
    expect(startedEl.querySelector('.sw-activity')!.textContent).toBe('▶ Started “Widget setup”')
  })
})

describe('MessageBubble feedback thumbs', () => {
  const noop = (): void => {}

  it('renders thumbs only for outbound agent (AI) messages', () => {
    const withThumbs = mount(
      <MessageBubble message={agentMsg} widgetKey="wk_t" onFeedback={noop} />,
    )
    expect(withThumbs.querySelectorAll('.sw-fb-btn')).toHaveLength(2)

    const contact = mount(<MessageBubble message={base} widgetKey="wk_t" onFeedback={noop} />)
    expect(contact.querySelectorAll('.sw-fb-btn')).toHaveLength(0)

    const human: UiMessage = { ...agentMsg, id: 'h1', author_type: 'user' }
    const humanEl = mount(<MessageBubble message={human} widgetKey="wk_t" onFeedback={noop} />)
    expect(humanEl.querySelectorAll('.sw-fb-btn')).toHaveLength(0)

    // Without the onFeedback wiring (e.g. previews) no thumbs render either.
    const unwired = mount(<MessageBubble message={agentMsg} widgetKey="wk_t" />)
    expect(unwired.querySelectorAll('.sw-fb-btn')).toHaveLength(0)
  })

  it('click posts the rating, persists it, and shows a thanks flash', async () => {
    const ratings: Array<[string, FeedbackRating]> = []
    const el = mount(
      <MessageBubble
        message={agentMsg}
        widgetKey="wk_t"
        onFeedback={(id, rating) => ratings.push([id, rating])}
      />,
    )
    const up = el.querySelector('button[aria-label="Helpful"]') as HTMLButtonElement
    up.click()
    await tick()
    expect(ratings).toEqual([['ai1', 'up']])
    expect(window.localStorage.getItem(feedbackStorageKey('wk_t', 'ai1'))).toBe('up')
    expect(up.getAttribute('aria-pressed')).toBe('true')
    expect(el.textContent).toContain('Thanks for the feedback')
  })

  it('restores the persisted rating on remount and allows switching', async () => {
    window.localStorage.setItem(feedbackStorageKey('wk_t', 'ai1'), 'up')
    const ratings: Array<[string, FeedbackRating]> = []
    const el = mount(
      <MessageBubble
        message={agentMsg}
        widgetKey="wk_t"
        onFeedback={(id, rating) => ratings.push([id, rating])}
      />,
    )
    const up = el.querySelector('button[aria-label="Helpful"]') as HTMLButtonElement
    const down = el.querySelector('button[aria-label="Not helpful"]') as HTMLButtonElement
    // Survives re-renders/reopens: pressed state comes back from localStorage.
    expect(up.getAttribute('aria-pressed')).toBe('true')

    // Clicking the already-selected thumb is a no-op.
    up.click()
    await tick()
    expect(ratings).toEqual([])

    // Switching re-posts and updates the stored rating (backend upserts).
    down.click()
    await tick()
    expect(ratings).toEqual([['ai1', 'down']])
    expect(window.localStorage.getItem(feedbackStorageKey('wk_t', 'ai1'))).toBe('down')
    expect(down.getAttribute('aria-pressed')).toBe('true')
    expect(up.getAttribute('aria-pressed')).toBe('false')
  })
})
