/**
 * Host-DOM onboarding checklist ("Getting started") — launcher pill + panel.
 *
 * Rendered directly in the host page (like the tour player, not in the
 * messenger iframe) so it can coexist with the messenger launcher and start
 * tours on the real DOM.
 *
 * Progress is dual-sourced: identified visitors have it stored server-side (the
 * loader POSTs each toggle), anonymous visitors keep it in localStorage under
 * `stept:checklist:{widgetKey}:{id}`. Both are merged on mount so a visitor who
 * logs in mid-onboarding never loses a tick. `url_visited` items are checked on
 * every SPA url change and `tour_completed` items optimistically when the
 * player completes that tour (the server does it authoritatively for identified
 * contacts).
 */

import { renderMarkdown } from './app/md'
import { globMatch } from './loader-core'
import { absolutizeMedia } from './tour-player'
import type { Checklist, ChecklistItem, ChecklistProgress } from './types'

// --- pure state helpers (unit-tested) ---------------------------------------

export function checklistStateKey(widgetKey: string, checklistId: string): string {
  return `stept:checklist:${widgetKey}:${checklistId}`
}

export function checklistOpenedKey(widgetKey: string, checklistId: string): string {
  return `stept:checklist-opened:${widgetKey}:${checklistId}`
}

export function emptyProgress(): ChecklistProgress {
  return { item_state: {}, dismissed: false, completed: false }
}

export function readChecklistState(
  storage: Pick<Storage, 'getItem'> | null,
  key: string,
): ChecklistProgress {
  try {
    const raw = storage?.getItem(key)
    if (!raw) return emptyProgress()
    const parsed = JSON.parse(raw) as Partial<ChecklistProgress>
    const state = parsed?.item_state
    return {
      item_state:
        state && typeof state === 'object'
          ? Object.fromEntries(
              Object.entries(state).filter(([, v]) => typeof v === 'string') as [string, string][],
            )
          : {},
      dismissed: Boolean(parsed?.dismissed),
      completed: Boolean(parsed?.completed),
    }
  } catch {
    return emptyProgress()
  }
}

export function writeChecklistState(
  storage: Pick<Storage, 'setItem'> | null,
  key: string,
  state: ChecklistProgress,
): void {
  try {
    storage?.setItem(key, JSON.stringify(state))
  } catch {
    /* private mode — progress is session-only */
  }
}

/** Union of server + local progress: a tick from either side counts. */
export function mergeChecklistProgress(
  server: ChecklistProgress | undefined | null,
  local: ChecklistProgress,
): ChecklistProgress {
  return {
    item_state: { ...local.item_state, ...(server?.item_state ?? {}) },
    dismissed: Boolean(server?.dismissed) || local.dismissed,
    completed: Boolean(server?.completed) || local.completed,
  }
}

/** Item ids newly satisfied by landing on `url` (completion.type url_visited). */
export function urlCompletedItems(
  items: readonly ChecklistItem[],
  url: string,
  state: ChecklistProgress,
): string[] {
  return items
    .filter(
      (item) =>
        item.completion?.type === 'url_visited' &&
        !state.item_state[item.id] &&
        Boolean(item.completion.url_pattern) &&
        globMatch(item.completion.url_pattern as string, url),
    )
    .map((item) => item.id)
}

/** Item ids newly satisfied by completing `tourId`. */
export function tourCompletedItems(
  items: readonly ChecklistItem[],
  tourId: string,
  state: ChecklistProgress,
): string[] {
  return items
    .filter(
      (item) =>
        item.completion?.type === 'tour_completed' &&
        item.completion.tour_id === tourId &&
        !state.item_state[item.id],
    )
    .map((item) => item.id)
}

export function completedCount(
  items: readonly ChecklistItem[],
  state: ChecklistProgress,
): number {
  return items.filter((item) => Boolean(state.item_state[item.id])).length
}

// --- the DOM widget ---------------------------------------------------------

export interface ChecklistWidgetOptions {
  widgetKey: string
  doc?: Document
  win?: Window & typeof globalThis
  storage?: Storage | null
  /** Widget API origin — root-relative media in item bodies resolves against it. */
  apiBase?: string
  /** Run an item's CTA (start_tour / open_url / open_messenger). */
  onAction?: (item: ChecklistItem, checklist: Checklist) => void
  /** Report a toggle to the backend; resolves false when it wasn't stored. */
  onProgress?: (checklistId: string, itemId: string, done: boolean) => void
  onDismiss?: (checklistId: string) => void
  /** The loader keeps at most one overlay open at a time. */
  onOpenChange?: (open: boolean) => void
}

const STYLE_ID = 'stept-checklist-style'
const CSS = `
.stept-cl-pill{position:fixed;bottom:20px;z-index:2147482890;display:flex;align-items:center;gap:8px;
  border:0;border-radius:99px;padding:10px 16px;cursor:pointer;
  background:var(--stept-accent,#5b46e5);color:#fff;font:600 13px/1.2 -apple-system,BlinkMacSystemFont,
  "Segoe UI",Roboto,Helvetica,Arial,sans-serif;box-shadow:0 6px 20px rgba(15,23,42,.28)}
.stept-cl-pill:hover{transform:translateY(-1px)}
.stept-cl-pill[hidden],.stept-cl-panel[hidden]{display:none}
.stept-cl-right{right:92px}
.stept-cl-left{left:92px}
.stept-cl-pill .stept-cl-pill-count{background:rgba(255,255,255,.25);border-radius:99px;padding:1px 7px;font-size:11px}
.stept-cl-panel{position:fixed;bottom:76px;z-index:2147482891;width:340px;max-width:calc(100vw - 32px);
  max-height:min(70vh,520px);overflow:auto;box-sizing:border-box;border-radius:16px;background:#fff;color:#0f172a;
  box-shadow:0 16px 48px rgba(15,23,42,.28);padding:16px;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.stept-cl-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin:0 0 4px}
.stept-cl-head h3{margin:0;font-size:15px;font-weight:600}
.stept-cl-desc{margin:0 0 10px;font-size:13px;color:#64748b}
.stept-cl-close{border:0;background:transparent;color:#94a3b8;font-size:16px;line-height:1;cursor:pointer;padding:2px 4px}
.stept-cl-progress{display:flex;align-items:center;gap:8px;margin:0 0 12px;font-size:12px;color:#64748b}
.stept-cl-bar{flex:1;height:6px;border-radius:3px;background:rgba(100,116,139,.2);overflow:hidden}
.stept-cl-bar i{display:block;height:100%;background:var(--stept-accent,#5b46e5);transition:width .2s ease}
.stept-cl-items{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.stept-cl-item{border-radius:10px;padding:8px}
.stept-cl-item:hover{background:rgba(100,116,139,.08)}
.stept-cl-row{display:flex;align-items:center;gap:10px}
.stept-cl-check{flex:0 0 auto;width:20px;height:20px;border-radius:50%;border:2px solid rgba(100,116,139,.45);
  background:transparent;cursor:pointer;padding:0;color:#fff;font-size:12px;line-height:16px}
.stept-cl-item.done .stept-cl-check{background:var(--stept-accent,#5b46e5);border-color:var(--stept-accent,#5b46e5)}
.stept-cl-title{flex:1;text-align:left;border:0;background:transparent;cursor:pointer;padding:0;font:inherit;color:inherit}
.stept-cl-item.done .stept-cl-title{color:#94a3b8;text-decoration:line-through}
.stept-cl-cta{border:0;border-radius:8px;padding:5px 12px;font:600 12px/1.2 inherit;cursor:pointer;
  background:var(--stept-accent,#5b46e5);color:#fff}
.stept-cl-body{margin:6px 0 0 30px;font-size:13px;color:#475569}
.stept-cl-body[hidden]{display:none}
.stept-cl-body p{margin:0 0 6px}
.stept-cl-body img{max-width:100%;height:auto;border-radius:6px}
@media (prefers-color-scheme:dark){
  .stept-cl-panel{background:#1e293b;color:#f1f5f9}
  .stept-cl-body{color:#cbd5e1}
  .stept-cl-item:hover{background:rgba(148,163,184,.14)}
}
@media (max-width:480px){
  .stept-cl-panel{left:16px;right:16px;width:auto}
}
`

export class ChecklistWidget {
  private doc: Document
  private win: Window & typeof globalThis
  private widgetKey: string
  private storage: Storage | null
  private opts: ChecklistWidgetOptions

  private checklist: Checklist | null = null
  private state: ChecklistProgress = emptyProgress()
  private pill: HTMLElement | null = null
  private panel: HTMLElement | null = null
  private open = false
  private expanded = new Set<string>()

  constructor(opts: ChecklistWidgetOptions) {
    this.opts = opts
    this.doc = opts.doc ?? document
    this.win = opts.win ?? (this.doc.defaultView as Window & typeof globalThis) ?? window
    this.widgetKey = opts.widgetKey
    this.storage = opts.storage !== undefined ? opts.storage : safeLocalStorage(this.win)
  }

  get isOpen(): boolean {
    return this.open
  }

  get checklistId(): string | null {
    return this.checklist?.id ?? null
  }

  get progress(): ChecklistProgress {
    return { item_state: { ...this.state.item_state }, dismissed: this.state.dismissed, completed: this.state.completed }
  }

  /**
   * Render (or re-render) the launcher for this checklist.
   *
   * `autoOpen:false` keeps the pill but suppresses the first-visit auto-open —
   * the loader passes it when a tour or survey already owns the screen.
   */
  mount(checklist: Checklist, opts: { autoOpen?: boolean } = {}): void {
    const changed = this.checklist?.id !== checklist.id
    this.checklist = checklist
    if (changed) this.expanded.clear()
    this.state = mergeChecklistProgress(
      checklist.progress,
      readChecklistState(this.storage, this.stateKey()),
    )
    this.persist()
    if (this.state.dismissed) {
      this.unmount()
      return
    }
    this.ensureStyle()
    this.build()
    this.render()
    if (
      opts.autoOpen !== false &&
      checklist.launcher?.auto_open_once !== false &&
      !this.wasAutoOpened()
    ) {
      this.markAutoOpened()
      this.openPanel()
    }
  }

  unmount(): void {
    this.pill?.remove()
    this.panel?.remove()
    this.pill = this.panel = null
    if (this.open) {
      this.open = false
      this.opts.onOpenChange?.(false)
    }
  }

  openPanel(): void {
    if (!this.panel || this.open) return
    this.open = true
    this.panel.hidden = false
    this.render()
    this.opts.onOpenChange?.(true)
  }

  closePanel(): void {
    if (!this.open) return
    this.open = false
    if (this.panel) this.panel.hidden = true
    this.opts.onOpenChange?.(false)
  }

  toggle(): void {
    if (this.open) this.closePanel()
    else this.openPanel()
  }

  /** Auto-check `url_visited` items. Called on boot and every SPA url change. */
  checkUrl(url: string): void {
    if (!this.checklist) return
    for (const id of urlCompletedItems(this.checklist.items, url, this.state)) {
      this.setItemDone(id, true)
    }
  }

  /** Optimistically check `tour_completed` items when the player finishes. */
  onTourCompleted(tourId: string): void {
    if (!this.checklist) return
    for (const id of tourCompletedItems(this.checklist.items, tourId, this.state)) {
      this.setItemDone(id, true)
    }
  }

  setItemDone(itemId: string, done: boolean, report = true): void {
    if (!this.checklist) return
    if (Boolean(this.state.item_state[itemId]) === done) return
    if (done) this.state.item_state[itemId] = new Date().toISOString()
    else delete this.state.item_state[itemId]
    this.state.completed =
      completedCount(this.checklist.items, this.state) === this.checklist.items.length
    this.persist()
    this.render()
    if (report) this.opts.onProgress?.(this.checklist.id, itemId, done)
  }

  dismiss(): void {
    const checklist = this.checklist
    if (!checklist) return
    const confirmFn = typeof this.win.confirm === 'function' ? this.win.confirm.bind(this.win) : null
    if (confirmFn && !confirmFn('Hide this checklist?')) return
    this.state.dismissed = true
    this.persist()
    this.unmount()
    this.opts.onDismiss?.(checklist.id)
  }

  // --- DOM -----------------------------------------------------------------

  private ensureStyle(): void {
    if (this.doc.getElementById(STYLE_ID)) return
    const style = this.doc.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    this.doc.head.appendChild(style)
  }

  private build(): void {
    if (this.pill && this.panel) return
    const side = this.checklist?.theme?.position === 'bottom-left' ? 'left' : 'right'
    const accent = this.checklist?.theme?.accent || '#5b46e5'

    const pill = node(this.doc, 'button', `stept-cl-pill stept-cl-${side}`)
    pill.style.setProperty('--stept-accent', accent)
    pill.setAttribute('aria-haspopup', 'dialog')
    pill.onclick = () => this.toggle()

    const panel = node(this.doc, 'div', `stept-cl-panel stept-cl-${side}`)
    panel.style.setProperty('--stept-accent', accent)
    panel.setAttribute('role', 'dialog')
    panel.setAttribute('aria-label', this.checklist?.name || 'Checklist')
    panel.hidden = !this.open

    this.doc.body.appendChild(pill)
    this.doc.body.appendChild(panel)
    this.pill = pill
    this.panel = panel
  }

  private render(): void {
    const checklist = this.checklist
    if (!checklist || !this.pill || !this.panel) return
    const total = checklist.items.length
    const done = completedCount(checklist.items, this.state)

    this.pill.innerHTML = ''
    const label = node(this.doc, 'span')
    label.textContent = checklist.launcher?.label || 'Getting started'
    const count = node(this.doc, 'span', 'stept-cl-pill-count')
    count.textContent = `${done}/${total}`
    this.pill.appendChild(label)
    this.pill.appendChild(count)
    this.pill.setAttribute('aria-expanded', this.open ? 'true' : 'false')

    if (!this.open) return
    this.panel.innerHTML = ''
    const head = node(this.doc, 'div', 'stept-cl-head')
    const title = node(this.doc, 'h3')
    title.textContent = checklist.name
    head.appendChild(title)
    const close = node(this.doc, 'button', 'stept-cl-close')
    close.textContent = '×'
    close.setAttribute('aria-label', 'Hide checklist')
    close.onclick = () => this.dismiss()
    head.appendChild(close)
    this.panel.appendChild(head)

    if (checklist.description) {
      const desc = node(this.doc, 'p', 'stept-cl-desc')
      desc.textContent = checklist.description
      this.panel.appendChild(desc)
    }

    const progress = node(this.doc, 'div', 'stept-cl-progress')
    const bar = node(this.doc, 'div', 'stept-cl-bar')
    const fill = node(this.doc, 'i')
    fill.style.width = `${total ? Math.round((done / total) * 100) : 0}%`
    bar.appendChild(fill)
    const text = node(this.doc, 'span')
    text.textContent = `${done} of ${total}`
    progress.appendChild(bar)
    progress.appendChild(text)
    this.panel.appendChild(progress)

    const list = node(this.doc, 'ul', 'stept-cl-items')
    for (const item of checklist.items) list.appendChild(this.renderItem(item, checklist))
    this.panel.appendChild(list)
  }

  private renderItem(item: ChecklistItem, checklist: Checklist): HTMLElement {
    const isDone = Boolean(this.state.item_state[item.id])
    const li = node(this.doc, 'li', `stept-cl-item${isDone ? ' done' : ''}`)
    const row = node(this.doc, 'div', 'stept-cl-row')

    const check = node(this.doc, 'button', 'stept-cl-check')
    check.textContent = isDone ? '✓' : ''
    check.setAttribute('role', 'checkbox')
    check.setAttribute('aria-checked', isDone ? 'true' : 'false')
    check.setAttribute('aria-label', item.title)
    check.onclick = () => this.setItemDone(item.id, !isDone)
    row.appendChild(check)

    const title = node(this.doc, 'button', 'stept-cl-title')
    title.textContent = item.title
    const expanded = this.expanded.has(item.id)
    title.setAttribute('aria-expanded', expanded ? 'true' : 'false')
    title.onclick = () => {
      if (this.expanded.has(item.id)) this.expanded.delete(item.id)
      else this.expanded.add(item.id)
      this.render()
    }
    row.appendChild(title)

    if (item.action && item.action.type !== 'none') {
      const cta = node(this.doc, 'button', 'stept-cl-cta')
      cta.textContent = ctaLabel(item)
      cta.onclick = () => this.opts.onAction?.(item, checklist)
      row.appendChild(cta)
    }
    li.appendChild(row)

    if (item.body) {
      const body = node(this.doc, 'div', 'stept-cl-body')
      body.hidden = !expanded
      body.innerHTML = renderMarkdown(item.body)
      absolutizeMedia(body, this.opts.apiBase ?? '')
      li.appendChild(body)
    }
    return li
  }

  // --- storage -------------------------------------------------------------

  private stateKey(): string {
    return checklistStateKey(this.widgetKey, this.checklist?.id ?? '')
  }

  private persist(): void {
    if (!this.checklist) return
    writeChecklistState(this.storage, this.stateKey(), this.state)
  }

  private wasAutoOpened(): boolean {
    try {
      return this.storage?.getItem(checklistOpenedKey(this.widgetKey, this.checklist?.id ?? '')) === '1'
    } catch {
      return false
    }
  }

  private markAutoOpened(): void {
    try {
      this.storage?.setItem(checklistOpenedKey(this.widgetKey, this.checklist?.id ?? ''), '1')
    } catch {
      /* ignore */
    }
  }
}

function ctaLabel(item: ChecklistItem): string {
  switch (item.action?.type) {
    case 'start_tour':
      return 'Start'
    case 'open_url':
      return 'Open'
    case 'open_messenger':
      return 'Chat'
    default:
      return 'Go'
  }
}

function node(doc: Document, tag: string, className = ''): HTMLElement {
  const el = doc.createElement(tag)
  if (className) el.className = className
  return el
}

function safeLocalStorage(win: Window): Storage | null {
  try {
    return win.localStorage ?? null
  } catch {
    return null
  }
}
