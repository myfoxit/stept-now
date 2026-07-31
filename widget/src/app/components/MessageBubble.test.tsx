import { render } from 'preact'
import { afterEach, describe, expect, it } from 'vitest'

import type { UiMessage } from '../controller'
import { MessageBubble } from './MessageBubble'

let container: HTMLDivElement | null = null

function mount(node: ReturnType<typeof MessageBubble>): HTMLDivElement {
  container = document.createElement('div')
  document.body.appendChild(container)
  render(node, container)
  return container
}

afterEach(() => {
  if (container) {
    render(null, container)
    container.remove()
    container = null
  }
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
