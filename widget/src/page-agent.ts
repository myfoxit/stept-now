/**
 * Host-page agent runtime — the AI's hands and eyes inside the customer's app.
 *
 * The old repo drove pages from a Chrome extension over `chrome.debugger`
 * (`extension/src/drive-controller.ts`). An embedded widget has no debugger and
 * no extension to install, so the same op set is re-implemented against the
 * host DOM the loader already lives in:
 *
 *     snapshot | find | read | act | navigate | scroll | wait
 *
 * Each op answers with a fresh compact view of the page (`@stept/dom-capture`
 * `serializeCompact`), so the model always decides against what is on screen
 * now, and every mutating op reports whether the page visibly changed —
 * the old controller's "the click may have hit a hidden duplicate" advisory,
 * which is what stops a driving model from confidently reporting a no-op.
 *
 * Everything here is a capability handed to a remote model, so the guardrails
 * are part of the contract, not decoration:
 *
 *  - the widget's own DOM (launcher, iframe, tour/checklist/survey overlays) is
 *    invisible to the index and to page reads — the AI can never click itself;
 *  - password fields are never typed into and never read (`formState` masks);
 *  - anything under `[data-stept-no-ai]` is excluded, so a host page can fence
 *    off destructive controls;
 *  - navigation is same-origin unless the host opts extra origins in;
 *  - the caller (controller.ts) gates all of it behind explicit user consent.
 *
 * `runPageOp` is a pure-ish function of (document, op) so it unit-tests in jsdom.
 */

import {
  buildTarget,
  findByText,
  INDEX_ATTR,
  indexInteractive,
  isOnScreen,
  pageText,
  serializeCompact,
  simpleProjection,
  stampIndex,
  type FoundElement,
  type IndexedElement,
  type Target,
} from '@stept/dom-capture'

import { fillElement } from './tour-player'

/** Ops the backend agent can ask the host page to perform. */
export type PageOpName = 'snapshot' | 'find' | 'read' | 'act' | 'navigate' | 'scroll' | 'wait'

export type PageActKind = 'click' | 'fill' | 'select' | 'check' | 'uncheck' | 'hover' | 'submit'

export interface PageOp {
  op: PageOpName
  args?: {
    /** snapshot: continue a paged listing from this element position. */
    offset?: number
    /** find: text to look for. */
    query?: string
    limit?: number
    /** read: cap on returned characters. */
    max_chars?: number
    /** act: which element (from the latest snapshot) and what to do to it. */
    index?: number
    kind?: PageActKind
    text?: string
    submit?: boolean
    /** navigate: destination URL. */
    url?: string
    /** scroll. */
    dir?: 'up' | 'down'
    amount?: number
    /** wait. */
    ms?: number
  }
}

export interface PageOpResult {
  ok: boolean
  /** Present on every op — the model always sees where it is. */
  url: string
  title: string
  /** Compact interactive listing (omitted by `read`, which returns prose). */
  elements?: string
  count?: number
  /** Advisory lines: no visible change, element off screen, navigation blocked… */
  note?: string
  /** `read` only. */
  text?: string
  /** `find` only. */
  found?: FoundElement[]
  /** Set when the op could not be performed at all. */
  error?: string
}

export interface PageAgentOptions {
  doc?: Document
  win?: Window & typeof globalThis
  /** Extra origins the AI may navigate to (the host's own origin is implicit). */
  allowedOrigins?: string[]
  /** Cap on indexed elements per snapshot. */
  indexCap?: number
  /** Char budget for a serialized listing. */
  maxElementChars?: number
  /** Char budget for `read`. */
  maxTextChars?: number
  /** Quiet-window settle before serializing (0 disables — tests). */
  settleMs?: number
}

/**
 * Everything the widget itself renders, excluded from every index and page read.
 *
 * Matched by class PREFIX rather than an enumerated list. An earlier version
 * listed the surfaces by name and silently missed the checklist pill (which uses
 * `stept-cl-*`, not `stept-checklist`), so the assistant could see — and offer to
 * click — the widget's own "Hide checklist" button. Every widget element is in the
 * `stept-` namespace, so keying off that is both complete and future-proof.
 */
const WIDGET_SELECTORS = [
  '#stept-frame',
  '#stept-launcher',
  '[class^="stept-"]',
  '[class*=" stept-"]',
  '[data-stept-widget]',
].join(',')

/** Host-page opt-out: anything inside this is fenced off from the AI. */
const OPT_OUT_SELECTOR = '[data-stept-no-ai]'

const DEFAULT_INDEX_CAP = 500
const DEFAULT_ELEMENT_CHARS = 7000
const DEFAULT_TEXT_CHARS = 6000
const MAX_WAIT_MS = 8000

/** Elements the AI must never act on or read. */
export function isOffLimits(el: Element): boolean {
  return Boolean(el.closest(`${WIDGET_SELECTORS},${OPT_OUT_SELECTOR}`))
}

/** A password input is off limits for writes even when it is otherwise visible. */
function isPasswordField(el: Element): boolean {
  return el.tagName === 'INPUT' && (el as HTMLInputElement).type === 'password'
}

/**
 * Wait for the DOM to go quiet — a settle signal instead of a fixed sleep.
 * Resolves once no mutation has fired for `quietMs`, or at `maxMs`. Ported from
 * the extension's `dom-settle.ts` so the widget never serializes a page that is
 * still mid-render (SPA route change, async list) and hands the model stale
 * indexes.
 */
export function waitForDomSettle(
  doc: Document,
  quietMs = 180,
  maxMs = 1200,
): Promise<void> {
  if (quietMs <= 0) return Promise.resolve()
  const view = doc.defaultView
  const Observer = view?.MutationObserver
  return new Promise<void>((resolve) => {
    let quietTimer: ReturnType<typeof setTimeout>
    let observer: MutationObserver | null = null
    let done = false
    const finish = (): void => {
      if (done) return
      done = true
      clearTimeout(quietTimer)
      clearTimeout(hardTimer)
      observer?.disconnect()
      resolve()
    }
    const bump = (): void => {
      clearTimeout(quietTimer)
      quietTimer = setTimeout(finish, quietMs)
    }
    const hardTimer = setTimeout(finish, maxMs)
    if (Observer) {
      try {
        observer = new Observer(bump)
        observer.observe(doc.documentElement ?? doc, {
          childList: true,
          subtree: true,
          attributes: true,
          characterData: true,
        })
      } catch {
        /* exotic env — the quiet countdown below still applies */
      }
    }
    bump()
  })
}

/**
 * The host-page executor. One instance per loader; it remembers the last
 * serialization so a mutating op can tell the model whether anything actually
 * changed, and keeps the index → element binding so an act can re-find its
 * target after an SPA re-render.
 */
export class PageAgent {
  private doc: Document
  private win: Window & typeof globalThis
  private options: PageAgentOptions
  /** index → element from the latest snapshot (the old repo's Cause-I fix). */
  private bound = new Map<number, Element>()
  private lastElements = ''
  private lastUrl = ''

  constructor(options: PageAgentOptions = {}) {
    this.doc = options.doc ?? document
    this.win = options.win ?? (window as Window & typeof globalThis)
    this.options = options
  }

  async run(op: PageOp): Promise<PageOpResult> {
    try {
      switch (op.op) {
        case 'snapshot':
          return await this.snapshot(op.args?.offset)
        case 'find':
          return await this.find(op.args?.query ?? '', op.args?.limit)
        case 'read':
          return await this.read(op.args?.max_chars)
        case 'act':
          return await this.act(op.args ?? {})
        case 'navigate':
          return await this.navigate(op.args?.url ?? '')
        case 'scroll':
          return await this.scroll(op.args?.dir, op.args?.amount)
        case 'wait':
          return await this.wait(op.args?.ms)
        default:
          return { ok: false, ...this.where(), error: `unknown op: ${String(op.op)}` }
      }
    } catch (err) {
      return {
        ok: false,
        ...this.where(),
        error: err instanceof Error ? err.message : String(err),
      }
    }
  }

  // --- reads ---------------------------------------------------------------

  private async snapshot(offset?: number, note?: string): Promise<PageOpResult> {
    await waitForDomSettle(this.doc, this.options.settleMs ?? 180)
    const index = this.reindex()
    const page = Math.max(0, Math.floor(offset ?? 0))
    const elements = serializeCompact(
      index,
      this.options.maxElementChars ?? DEFAULT_ELEMENT_CHARS,
      page,
    )
    // Only a page-one serialization is a valid baseline for the "did anything
    // change?" check — a paged view is a partial picture of the same DOM.
    if (page === 0) {
      this.lastElements = elements
      this.lastUrl = this.doc.location?.href ?? ''
    }
    return { ok: true, ...this.where(), elements, count: index.length, note }
  }

  private async find(query: string, limit?: number): Promise<PageOpResult> {
    const trimmed = query.trim()
    if (!trimmed) return { ok: false, ...this.where(), error: 'find needs a query' }
    await waitForDomSettle(this.doc, this.options.settleMs ?? 180)
    const index = this.reindex()
    const found = findByText(index, trimmed, Math.min(Math.max(limit ?? 10, 1), 50))
    return {
      ok: true,
      ...this.where(),
      found,
      count: index.length,
      note: found.length
        ? `${found.length} match(es) for "${trimmed}" — act on one by its index`
        : `nothing matching "${trimmed}" on this page; try page_read or scroll first`,
    }
  }

  private async read(maxChars?: number): Promise<PageOpResult> {
    const cap = Math.min(Math.max(maxChars ?? DEFAULT_TEXT_CHARS, 200), 20_000)
    const text = pageText(this.doc, cap, isOffLimits)
    return { ok: true, ...this.where(), text }
  }

  // --- writes --------------------------------------------------------------

  private async act(args: NonNullable<PageOp['args']>): Promise<PageOpResult> {
    const kind: PageActKind = args.kind ?? 'click'
    if (args.index === undefined || args.index === null) {
      return { ok: false, ...this.where(), error: 'act needs the element index from a snapshot' }
    }
    const el = this.resolveIndex(Math.floor(args.index))
    if (!el) {
      return {
        ok: false,
        ...this.where(),
        error: `no element at index ${args.index} — take a fresh page_snapshot (the page changed)`,
      }
    }
    if (isOffLimits(el)) {
      return { ok: false, ...this.where(), error: 'that element is not available to the assistant' }
    }
    if ((kind === 'fill' || kind === 'select') && isPasswordField(el)) {
      return {
        ok: false,
        ...this.where(),
        error: 'refusing to type into a password field — ask the person to enter it themselves',
      }
    }

    const before = { elements: this.lastElements, url: this.lastUrl }
    const html = el as HTMLElement
    html.scrollIntoView?.({ block: 'center', inline: 'center', behavior: 'instant' as ScrollBehavior })
    const notes: string[] = []
    if (!isOnScreen(el)) notes.push('the element was off screen and has been scrolled into view')

    switch (kind) {
      case 'click':
        html.click()
        break
      case 'hover':
        this.dispatchMouse(html, 'mouseover')
        this.dispatchMouse(html, 'mousemove')
        break
      case 'fill':
        fillElement(html, args.text ?? '')
        if (args.submit) this.pressEnter(html)
        break
      case 'select': {
        const chosen = this.selectOption(html, args.text ?? '')
        if (!chosen) {
          return {
            ok: false,
            ...this.where(),
            error: `no option matching "${args.text ?? ''}" on that control`,
          }
        }
        break
      }
      case 'check':
      case 'uncheck': {
        const input = html as HTMLInputElement
        const want = kind === 'check'
        if (typeof input.checked === 'boolean' && input.checked !== want) html.click()
        else if (typeof input.checked !== 'boolean') html.click()
        break
      }
      case 'submit': {
        const form = html.closest('form')
        if (!form) return { ok: false, ...this.where(), error: 'that element is not inside a form' }
        if (typeof (form as HTMLFormElement).requestSubmit === 'function') {
          ;(form as HTMLFormElement).requestSubmit()
        } else {
          ;(form as HTMLFormElement).submit()
        }
        break
      }
    }

    const result = await this.snapshot(0)
    // A click that changed NOTHING visible usually hit a hidden/inert duplicate
    // of the intended control. Say so rather than returning a silently identical
    // snapshot the model will read as success.
    if (
      kind === 'click' &&
      before.elements &&
      result.url === before.url &&
      result.elements === before.elements
    ) {
      notes.push(
        'no visible change after the click — it may have hit a hidden or inert element; ' +
          'try another match or page_read to check',
      )
    }
    if (notes.length) result.note = [result.note, ...notes].filter(Boolean).join('\n')
    return result
  }

  private async navigate(rawUrl: string): Promise<PageOpResult> {
    const target = rawUrl.trim()
    if (!target) return { ok: false, ...this.where(), error: 'navigate needs a url' }
    let resolved: URL
    try {
      resolved = new URL(target, this.doc.location?.href ?? undefined)
    } catch {
      return { ok: false, ...this.where(), error: `not a valid url: ${target}` }
    }
    if (!this.originAllowed(resolved.origin)) {
      return {
        ok: false,
        ...this.where(),
        error: `navigation to ${resolved.origin} is not allowed — only this site can be navigated`,
      }
    }
    this.win.location.assign(resolved.href)
    // The load tears this document down; the next op runs against the new page.
    return {
      ok: true,
      url: resolved.href,
      title: this.doc.title ?? '',
      note: 'navigating — call page_snapshot once the new page has loaded',
    }
  }

  private async scroll(dir?: 'up' | 'down', amount?: number): Promise<PageOpResult> {
    const distance = Math.min(Math.max(amount ?? 600, 50), 5000)
    const delta = dir === 'up' ? -distance : distance
    this.win.scrollBy?.({ top: delta, behavior: 'instant' as ScrollBehavior })
    return this.snapshot(0, `scrolled ${dir === 'up' ? 'up' : 'down'} ${distance}px`)
  }

  private async wait(ms?: number): Promise<PageOpResult> {
    const delay = Math.min(Math.max(ms ?? 500, 0), MAX_WAIT_MS)
    await new Promise((resolve) => setTimeout(resolve, delay))
    return this.snapshot(0, `waited ${delay}ms`)
  }

  // --- helpers -------------------------------------------------------------

  private where(): { url: string; title: string } {
    return { url: this.doc.location?.href ?? '', title: this.doc.title ?? '' }
  }

  private reindex(): IndexedElement[] {
    const index = indexInteractive(this.doc, {
      cap: this.options.indexCap ?? DEFAULT_INDEX_CAP,
      exclude: isOffLimits,
    })
    stampIndex(this.doc, index)
    this.bound = new Map(index.map((entry) => [entry.index, entry.el]))
    return index
  }

  /**
   * Re-find the element an index pointed at. Prefer the still-stamped node, then
   * the snapshot-bound node if it is still connected — an SPA that re-rendered
   * between the snapshot and the act wipes the attribute but often keeps the
   * node, and failing there would strand the model on "no element at index N".
   */
  private resolveIndex(index: number): Element | null {
    const stamped = this.doc.querySelector(`[${INDEX_ATTR}="${index}"]`)
    if (stamped?.isConnected) return stamped
    const cached = this.bound.get(index)
    if (cached?.isConnected) return cached
    return null
  }

  /**
   * Durable descriptor for an element the model addressed by `[index]`.
   *
   * An index is only valid until the next snapshot, but a walkthrough the model
   * composed has to survive the visitor clicking through it (and any re-render
   * that causes). So the index is converted, at guide-start time, into the same
   * `Target` + simple projection the tour recorder stores — which means the player
   * resolves and self-heals an AI-authored step exactly like a recorded one.
   */
  describe(index: number): {
    selector: string
    fallback_selectors: string[]
    text_hint: string
    target: Target
  } | null {
    const el = this.resolveIndex(Math.floor(index))
    if (!el || isOffLimits(el)) return null
    const target = buildTarget(el)
    const projection = simpleProjection(target)
    return {
      selector: projection.selector,
      fallback_selectors: projection.fallback_selectors,
      text_hint: projection.text_hint,
      target,
    }
  }

  private originAllowed(origin: string): boolean {
    const own = this.doc.location?.origin
    if (own && origin === own) return true
    return (this.options.allowedOrigins ?? []).includes(origin)
  }

  private dispatchMouse(el: HTMLElement, type: string): void {
    const view = this.win
    const Ctor = view.MouseEvent ?? MouseEvent
    el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: true, view: view as Window }))
  }

  private pressEnter(el: HTMLElement): void {
    const Ctor = this.win.KeyboardEvent ?? KeyboardEvent
    const init = { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true, cancelable: true }
    el.dispatchEvent(new Ctor('keydown', init))
    el.dispatchEvent(new Ctor('keyup', init))
    const form = el.closest('form')
    if (form && typeof (form as HTMLFormElement).requestSubmit === 'function') {
      ;(form as HTMLFormElement).requestSubmit()
    }
  }

  /** Choose an option on a `<select>` by label or value, then fire change. */
  private selectOption(el: HTMLElement, wanted: string): boolean {
    if (el.tagName !== 'SELECT') {
      fillElement(el, wanted)
      return true
    }
    const select = el as HTMLSelectElement
    const needle = wanted.trim().toLowerCase()
    const options = [...select.options]
    const match =
      options.find((option) => option.text.trim().toLowerCase() === needle) ??
      options.find((option) => option.value.trim().toLowerCase() === needle) ??
      options.find((option) => option.text.trim().toLowerCase().includes(needle))
    if (!match) return false
    select.value = match.value
    const Ctor = this.win.Event ?? Event
    select.dispatchEvent(new Ctor('input', { bubbles: true }))
    select.dispatchEvent(new Ctor('change', { bubbles: true }))
    return true
  }
}
