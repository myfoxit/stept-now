import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ChecklistWidget,
  checklistStateKey,
  completedCount,
  mergeChecklistProgress,
  readChecklistState,
  tourCompletedItems,
  urlCompletedItems,
  writeChecklistState,
} from './checklist-widget'
import type { Checklist, ChecklistItem } from './types'

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

function item(partial: Partial<ChecklistItem> & { id: string }): ChecklistItem {
  return {
    title: partial.id,
    body: '',
    action: { type: 'none' },
    completion: { type: 'manual' },
    ...partial,
  }
}

function checklistOf(items: ChecklistItem[], extra: Partial<Checklist> = {}): Checklist {
  return {
    id: 'cl-1',
    name: 'Getting started',
    description: 'Three quick things',
    items,
    theme: { accent: '#6366f1', position: 'bottom-right' },
    launcher: { label: 'Getting started', auto_open_once: true },
    version: 1,
    ...extra,
  }
}

const items = [
  item({ id: 'i1', title: 'Take the tour', completion: { type: 'tour_completed', tour_id: 't1' } }),
  item({
    id: 'i2',
    title: 'Connect an inbox',
    completion: { type: 'url_visited', url_pattern: '*/settings*' },
  }),
  item({ id: 'i3', title: 'Invite a teammate', action: { type: 'start_tour', tour_id: 't9' } }),
]

let widget: ChecklistWidget | null = null

afterEach(() => {
  widget?.unmount()
  widget = null
  document.body.innerHTML = ''
  document.getElementById('stept-checklist-style')?.remove()
  vi.restoreAllMocks()
})

describe('checklist state helpers', () => {
  it('namespaces local state per widget key + checklist and round-trips it', () => {
    const storage = memoryStorage()
    const key = checklistStateKey('wk_a', 'cl-1')
    expect(key).toBe('stept:checklist:wk_a:cl-1')
    writeChecklistState(storage, key, { item_state: { i1: 'now' }, dismissed: false, completed: false })
    expect(readChecklistState(storage, key).item_state).toEqual({ i1: 'now' })
    storage.setItem(key, 'not json')
    expect(readChecklistState(storage, key)).toEqual({ item_state: {}, dismissed: false, completed: false })
    expect(readChecklistState(null, key).item_state).toEqual({})
  })

  it('merges server and local progress — a tick from either side counts', () => {
    const merged = mergeChecklistProgress(
      { item_state: { i1: 'server' }, dismissed: false, completed: false },
      { item_state: { i2: 'local' }, dismissed: true, completed: false },
    )
    expect(merged.item_state).toEqual({ i1: 'server', i2: 'local' })
    expect(merged.dismissed).toBe(true)
  })

  it('finds url_visited items for the current URL, case-insensitively, once', () => {
    const empty = { item_state: {}, dismissed: false, completed: false }
    expect(urlCompletedItems(items, 'https://app.test/Settings/inboxes', empty)).toEqual(['i2'])
    expect(urlCompletedItems(items, 'https://app.test/billing', empty)).toEqual([])
    const done = { item_state: { i2: 'now' }, dismissed: false, completed: false }
    expect(urlCompletedItems(items, 'https://app.test/settings', done)).toEqual([])
  })

  it('finds tour_completed items for a finished tour', () => {
    const empty = { item_state: {}, dismissed: false, completed: false }
    expect(tourCompletedItems(items, 't1', empty)).toEqual(['i1'])
    expect(tourCompletedItems(items, 'other', empty)).toEqual([])
    expect(completedCount(items, { item_state: { i1: 'x' }, dismissed: false, completed: false })).toBe(1)
  })
})

describe('ChecklistWidget', () => {
  const pill = (): HTMLElement | null => document.querySelector('.stept-cl-pill')
  const panel = (): HTMLElement | null => document.querySelector('.stept-cl-panel')

  it('renders the launcher pill with progress and auto-opens exactly once', () => {
    const storage = memoryStorage()
    widget = new ChecklistWidget({ widgetKey: 'wk', storage })
    widget.mount(checklistOf(items))
    expect(pill()!.textContent).toContain('0/3')
    expect(widget.isOpen).toBe(true)
    expect(panel()!.textContent).toContain('Getting started')

    widget.closePanel()
    widget.mount(checklistOf(items)) // a later url change re-mounts
    expect(widget.isOpen).toBe(false)
  })

  it('suppresses the auto-open when another overlay owns the screen', () => {
    widget = new ChecklistWidget({ widgetKey: 'wk', storage: memoryStorage() })
    widget.mount(checklistOf(items), { autoOpen: false })
    expect(widget.isOpen).toBe(false)
    expect(pill()).toBeTruthy()
  })

  it('ticks an item, persists it locally and reports it to the caller', () => {
    const storage = memoryStorage()
    const reported: Array<[string, string, boolean]> = []
    widget = new ChecklistWidget({
      widgetKey: 'wk',
      storage,
      onProgress: (checklistId, itemId, done) => reported.push([checklistId, itemId, done]),
    })
    widget.mount(checklistOf(items))
    const check = panel()!.querySelector('.stept-cl-check') as HTMLButtonElement
    check.click()
    expect(reported).toEqual([['cl-1', 'i1', true]])
    expect(widget.progress.item_state.i1).toBeTruthy()
    // Anonymous fallback: the state survives a fresh mount with no server progress.
    expect(readChecklistState(storage, checklistStateKey('wk', 'cl-1')).item_state.i1).toBeTruthy()
    const fresh = new ChecklistWidget({ widgetKey: 'wk', storage })
    fresh.mount(checklistOf(items))
    expect(fresh.progress.item_state.i1).toBeTruthy()
    expect(document.querySelectorAll('.stept-cl-item.done')).toHaveLength(1)
    fresh.unmount()
  })

  it('auto-checks url_visited items on a url change and tour_completed on a finished tour', () => {
    const reported: string[] = []
    widget = new ChecklistWidget({
      widgetKey: 'wk',
      storage: memoryStorage(),
      onProgress: (_c, itemId) => reported.push(itemId),
    })
    widget.mount(checklistOf(items))
    widget.checkUrl('https://app.test/settings/inboxes')
    expect(widget.progress.item_state.i2).toBeTruthy()
    widget.checkUrl('https://app.test/settings/inboxes') // idempotent
    widget.onTourCompleted('t1')
    expect(widget.progress.item_state.i1).toBeTruthy()
    expect(reported).toEqual(['i2', 'i1'])
    expect(pill()!.textContent).toContain('2/3')
  })

  it('runs an item action through the caller', () => {
    const actions: string[] = []
    widget = new ChecklistWidget({
      widgetKey: 'wk',
      storage: memoryStorage(),
      onAction: (clicked) => actions.push(clicked.id),
    })
    widget.mount(checklistOf(items))
    const cta = panel()!.querySelector('.stept-cl-cta') as HTMLButtonElement
    expect(cta.textContent).toBe('Start')
    cta.click()
    expect(actions).toEqual(['i3'])
  })

  it('confirms before dismissing, then stays hidden on re-mount', () => {
    const storage = memoryStorage()
    const dismissed: string[] = []
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    widget = new ChecklistWidget({
      widgetKey: 'wk',
      storage,
      onDismiss: (id) => dismissed.push(id),
    })
    widget.mount(checklistOf(items))
    ;(panel()!.querySelector('.stept-cl-close') as HTMLButtonElement).click()
    expect(dismissed).toEqual([])
    expect(pill()).toBeTruthy()

    confirmSpy.mockReturnValue(true)
    ;(panel()!.querySelector('.stept-cl-close') as HTMLButtonElement).click()
    expect(dismissed).toEqual(['cl-1'])
    expect(pill()).toBeNull()

    widget.mount(checklistOf(items))
    expect(pill()).toBeNull()
  })

  it('expands an item body as rendered markdown', () => {
    widget = new ChecklistWidget({ widgetKey: 'wk', storage: memoryStorage() })
    widget.mount(checklistOf([item({ id: 'i1', title: 'Read me', body: 'Some **bold** help' })]))
    const body = panel()!.querySelector('.stept-cl-body') as HTMLElement
    expect(body.hidden).toBe(true)
    expect(body.innerHTML).toContain('<strong>bold</strong>')
    ;(panel()!.querySelector('.stept-cl-title') as HTMLButtonElement).click()
    expect((panel()!.querySelector('.stept-cl-body') as HTMLElement).hidden).toBe(false)
  })
})
