import { render, type ComponentChild } from 'preact'
import { afterEach, describe, expect, it } from 'vitest'

import type { Starter, TourState } from '../api-extra'
import { Thread } from './Thread'

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

const noop = (): void => {}

function threadProps() {
  return {
    messages: [],
    agentTyping: false,
    hasMore: false,
    loading: false,
    status: 'open' as string | null,
    csatDone: false,
    greeting: 'Hi there 👋',
    widgetKey: 'wk_t',
    aiEnabled: true,
    agentName: null as string | null,
    aiDisclosure: true,
    starters: null as Starter[] | null,
    tourState: null as TourState | null,
    humanRequested: false,
    pageControl: false,
    pageTitle: '',
    actionsAllowed: false,
    workingOnPage: null as string | null,
    pendingAction: null,
    onSend: noop as (text: string) => void,
    onTyping: noop,
    onLoadMore: noop,
    onCsat: noop,
    onFeedback: noop,
    onRetry: noop,
    onStartTour: noop as (id: string) => void,
    onResumeTour: noop as (id: string) => void,
    onRequestHuman: noop,
    onAllowActions: noop,
    onRunAction: noop,
    onDismissAction: noop,
  }
}

describe('Thread empty state', () => {
  it('makes the one AI promise and sends a starter chip as a message', () => {
    const sent: string[] = []
    const starters: Starter[] = [
      { kind: 'tour', text: 'Show me: Widget setup' },
      { kind: 'article', text: 'How do I invite teammates?' },
    ]
    const el = mount(
      <Thread {...threadProps()} starters={starters} onSend={(text) => sent.push(text)} />,
    )
    expect(el.textContent).toContain('Ask anything — our AI answers right away.')
    // The contradictory "we'll get back to you here" copy is gone for AI inboxes.
    expect(el.textContent).not.toContain('we’ll get back to you here')

    const chips = el.querySelectorAll('.sw-chip')
    expect(chips).toHaveLength(2)
    ;(chips[1] as HTMLButtonElement).click()
    expect(sent).toEqual(['How do I invite teammates?'])
  })

  it('keeps the human-inbox wording when there is no AI agent', () => {
    const el = mount(<Thread {...threadProps()} aiEnabled={false} />)
    expect(el.textContent).not.toContain('Ask anything — our AI answers right away.')
    expect(el.textContent).toContain('Send a message and we’ll get back to you here.')
  })
})

describe('Thread human handoff', () => {
  it('offers "Talk to a person" and swaps to expectation copy once requested', () => {
    let requested = 0
    const el = mount(<Thread {...threadProps()} onRequestHuman={() => requested++} />)
    const button = [...el.querySelectorAll('button')].find(
      (b) => b.textContent === 'Talk to a person',
    )
    expect(button).toBeDefined()
    button!.click()
    expect(requested).toBe(1)

    const pending = mount(<Thread {...threadProps()} humanRequested />)
    expect(pending.textContent).toContain(
      'A teammate will reply here — you’ll also see it in your conversations.',
    )
    expect(
      [...pending.querySelectorAll('button')].some((b) => b.textContent === 'Talk to a person'),
    ).toBe(false)
  })
})

describe('Thread live tour state', () => {
  it('renders progress as a system line', () => {
    const state: TourState = {
      status: 'step_viewed',
      tourId: 't1',
      step: 2,
      total: 5,
      title: 'Widget setup',
    }
    const el = mount(<Thread {...threadProps()} tourState={state} />)
    const line = el.querySelector('.sw-activity')
    expect(line).not.toBeNull()
    expect(line!.textContent).toContain('Started “Widget setup”')
    expect(line!.textContent).toContain('step 2 of 5')
  })

  it('shows the quiet snag hint (no fake agent message) and resumes', () => {
    let resumed = ''
    const state: TourState = { status: 'blocked', tourId: 't1', step: 2, total: 5, title: 'Setup' }
    const el = mount(
      <Thread {...threadProps()} tourState={state} onResumeTour={(id) => (resumed = id)} />,
    )
    expect(el.textContent).toContain('The tour hit a snag — want me to explain instead?')
    // Rendered as UI copy, not as a message bubble from the agent.
    expect(el.querySelector('.sw-bubble')).toBeNull()
    const resume = [...el.querySelectorAll('button')].find((b) => b.textContent === 'Resume tour')
    resume!.click()
    expect(resumed).toBe('t1')
  })
})
