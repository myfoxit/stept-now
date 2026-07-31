/**
 * Host-DOM product-tour player.
 *
 * Renders a spotlight + tooltip over real page elements (resolved via
 * `document.querySelector(step.selector)`), drives Back/Next/Done, and reports
 * lifecycle events through a callback. The positioning + eligibility math is
 * exported as pure functions so it can be unit-tested without a full browser.
 */

import type { Tour, TourEventName } from './types'

export type Side = 'top' | 'bottom' | 'left' | 'right'
export type Placement = 'auto' | Side

export interface Rect {
  top: number
  left: number
  width: number
  height: number
}
export interface Size {
  width: number
  height: number
}
export interface Viewport {
  width: number
  height: number
}

// --- pure geometry / eligibility (unit-tested) ------------------------------

/** For `auto`, choose the side with enough room, preferring below → above → right → left. */
export function resolveAutoPlacement(
  target: Rect,
  tip: Size,
  vp: Viewport,
  gap = 12,
): Side {
  const space = {
    bottom: vp.height - (target.top + target.height),
    top: target.top,
    right: vp.width - (target.left + target.width),
    left: target.left,
  }
  const needsV = tip.height + gap
  const needsH = tip.width + gap
  if (space.bottom >= needsV) return 'bottom'
  if (space.top >= needsV) return 'top'
  if (space.right >= needsH) return 'right'
  if (space.left >= needsH) return 'left'
  return (Object.entries(space).sort((a, b) => b[1] - a[1])[0]?.[0] as Side) ?? 'bottom'
}

/** Absolute viewport coords for the tooltip, clamped so it never leaves the screen. */
export function computeTooltipPosition(
  placement: Placement,
  target: Rect,
  tip: Size,
  vp: Viewport,
  gap = 12,
): { top: number; left: number; side: Side } {
  const side = placement === 'auto' ? resolveAutoPlacement(target, tip, vp, gap) : placement
  let top = 0
  let left = 0
  const cx = target.left + target.width / 2
  const cy = target.top + target.height / 2
  switch (side) {
    case 'bottom':
      top = target.top + target.height + gap
      left = cx - tip.width / 2
      break
    case 'top':
      top = target.top - tip.height - gap
      left = cx - tip.width / 2
      break
    case 'right':
      left = target.left + target.width + gap
      top = cy - tip.height / 2
      break
    case 'left':
      left = target.left - tip.width - gap
      top = cy - tip.height / 2
      break
  }
  const m = 8
  left = Math.max(m, Math.min(left, vp.width - tip.width - m))
  top = Math.max(m, Math.min(top, vp.height - tip.height - m))
  return { top, left, side }
}

/** First tour not already seen locally (client-side dedup for anon visitors). */
export function selectFirstEligibleTour(
  tours: Tour[],
  seen: Iterable<string> = [],
): Tour | null {
  const seenSet = seen instanceof Set ? seen : new Set(seen)
  for (const tour of tours) {
    if (!seenSet.has(tour.id) && tour.steps.length > 0) return tour
  }
  return null
}

// --- the DOM player ---------------------------------------------------------

export interface TourPlayerOptions {
  accent?: string
  /** Reports started / step_viewed / completed / dismissed to the caller. */
  onEvent?: (event: TourEventName, stepIndex: number | null) => void
  /** Injected for tests; defaults to the real global. */
  doc?: Document
  win?: Window & typeof globalThis
}

const STYLE_ID = 'stept-tour-style'
const CSS = `
.stept-tour-backdrop{position:fixed;inset:0;z-index:2147483000;pointer-events:none}
.stept-tour-hole{position:fixed;z-index:2147483000;border-radius:8px;
  box-shadow:0 0 0 9999px rgba(15,23,42,.55);transition:all .18s ease;pointer-events:none;
  outline:2px solid var(--stept-accent,#6366f1);outline-offset:2px}
.stept-tour-tip{position:fixed;z-index:2147483001;max-width:320px;width:calc(100vw - 32px);
  background:#fff;color:#0f172a;border-radius:12px;box-shadow:0 12px 40px rgba(15,23,42,.28);
  padding:16px 16px 12px;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  box-sizing:border-box}
.stept-tour-tip h4{margin:0 0 6px;font-size:15px;font-weight:600}
.stept-tour-tip p{margin:0 0 12px;color:#334155}
.stept-tour-tip .stept-tour-foot{display:flex;align-items:center;justify-content:space-between;gap:8px}
.stept-tour-tip .stept-tour-count{font-size:12px;color:#64748b}
.stept-tour-tip .stept-tour-actions{display:flex;gap:8px}
.stept-tour-btn{border:0;border-radius:8px;padding:7px 14px;font-size:13px;font-weight:600;cursor:pointer}
.stept-tour-btn.primary{background:var(--stept-accent,#6366f1);color:#fff}
.stept-tour-btn.ghost{background:transparent;color:#475569}
.stept-tour-close{position:absolute;top:8px;right:8px;border:0;background:transparent;
  font-size:16px;line-height:1;cursor:pointer;color:#94a3b8;padding:4px}
@media (prefers-color-scheme:dark){
  .stept-tour-tip{background:#1e293b;color:#f1f5f9}
  .stept-tour-tip p{color:#cbd5e1}
}
`

export class TourPlayer {
  private doc: Document
  private win: Window & typeof globalThis
  private accent: string
  private onEvent: (event: TourEventName, stepIndex: number | null) => void

  private tour: Tour | null = null
  private index = 0
  private root: HTMLElement | null = null
  private hole: HTMLElement | null = null
  private tip: HTMLElement | null = null
  private reflow = () => this.position()

  constructor(opts: TourPlayerOptions = {}) {
    this.doc = opts.doc ?? document
    this.win = opts.win ?? (window as Window & typeof globalThis)
    this.accent = opts.accent ?? '#6366f1'
    this.onEvent = opts.onEvent ?? (() => {})
  }

  get active(): boolean {
    return this.tour !== null
  }

  start(tour: Tour): void {
    if (this.tour) this.teardown()
    if (!tour.steps.length) return
    this.tour = tour
    this.index = 0
    this.accent = tour.theme?.accent || this.accent
    this.ensureStyle()
    this.build()
    this.onEvent('started', null)
    this.showStep(0)
    this.win.addEventListener('resize', this.reflow)
    this.win.addEventListener('scroll', this.reflow, true)
  }

  next(): void {
    if (!this.tour) return
    const nextExisting = this.nextExistingIndex(this.index + 1)
    if (nextExisting === -1) {
      this.finish('completed')
    } else {
      this.showStep(nextExisting)
    }
  }

  back(): void {
    if (!this.tour) return
    const prev = this.prevExistingIndex(this.index - 1)
    if (prev !== -1) this.showStep(prev)
  }

  dismiss(): void {
    this.finish('dismissed')
  }

  private finish(event: TourEventName): void {
    if (!this.tour) return
    this.onEvent(event, event === 'dismissed' ? this.index : null)
    this.teardown()
  }

  private nextExistingIndex(from: number): number {
    if (!this.tour) return -1
    for (let i = from; i < this.tour.steps.length; i++) {
      if (this.doc.querySelector(this.tour.steps[i]!.selector)) return i
    }
    return -1
  }

  private prevExistingIndex(from: number): number {
    if (!this.tour) return -1
    for (let i = from; i >= 0; i--) {
      if (this.doc.querySelector(this.tour.steps[i]!.selector)) return i
    }
    return -1
  }

  private showStep(i: number): void {
    if (!this.tour) return
    this.index = i
    const step = this.tour.steps[i]!
    const target = this.doc.querySelector(step.selector) as HTMLElement | null
    if (!target) {
      // Target vanished — advance rather than dead-ending the tour.
      this.next()
      return
    }
    if (typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
    }
    this.renderTip(step, i)
    this.position()
    this.onEvent('step_viewed', i)
  }

  private ensureStyle(): void {
    if (this.doc.getElementById(STYLE_ID)) return
    const style = this.doc.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    this.doc.head.appendChild(style)
  }

  private build(): void {
    const root = this.doc.createElement('div')
    root.className = 'stept-tour-backdrop'
    root.style.setProperty('--stept-accent', this.accent)
    const hole = this.doc.createElement('div')
    hole.className = 'stept-tour-hole'
    const tip = this.doc.createElement('div')
    tip.className = 'stept-tour-tip'
    tip.setAttribute('role', 'dialog')
    root.appendChild(hole)
    // Tip is a sibling on <body> so it can receive pointer events.
    this.doc.body.appendChild(root)
    this.doc.body.appendChild(tip)
    tip.style.setProperty('--stept-accent', this.accent)
    this.root = root
    this.hole = hole
    this.tip = tip
  }

  private renderTip(step: { title: string; body: string }, i: number): void {
    if (!this.tip || !this.tour) return
    const total = this.tour.steps.length
    const isLast = this.nextExistingIndex(i + 1) === -1
    const hasPrev = this.prevExistingIndex(i - 1) !== -1
    this.tip.innerHTML = ''
    const close = el(this.doc, 'button', 'stept-tour-close')
    close.textContent = '×'
    close.setAttribute('aria-label', 'Dismiss tour')
    close.onclick = () => this.dismiss()
    const h = el(this.doc, 'h4')
    h.textContent = step.title || `Step ${i + 1}`
    const p = el(this.doc, 'p')
    p.textContent = step.body || ''
    const foot = el(this.doc, 'div', 'stept-tour-foot')
    const count = el(this.doc, 'span', 'stept-tour-count')
    count.textContent = `${i + 1} of ${total}`
    const actions = el(this.doc, 'div', 'stept-tour-actions')
    if (hasPrev) {
      const back = el(this.doc, 'button', 'stept-tour-btn ghost')
      back.textContent = 'Back'
      back.onclick = () => this.back()
      actions.appendChild(back)
    }
    const nextBtn = el(this.doc, 'button', 'stept-tour-btn primary')
    nextBtn.textContent = isLast ? 'Done' : 'Next'
    nextBtn.onclick = () => this.next()
    actions.appendChild(nextBtn)
    foot.appendChild(count)
    foot.appendChild(actions)
    this.tip.appendChild(close)
    this.tip.appendChild(h)
    if (step.body) this.tip.appendChild(p)
    this.tip.appendChild(foot)
  }

  private position(): void {
    if (!this.tour || !this.hole || !this.tip) return
    const step = this.tour.steps[this.index]!
    const target = this.doc.querySelector(step.selector) as HTMLElement | null
    if (!target) return
    const r = target.getBoundingClientRect()
    const pad = 6
    Object.assign(this.hole.style, {
      top: `${r.top - pad}px`,
      left: `${r.left - pad}px`,
      width: `${r.width + pad * 2}px`,
      height: `${r.height + pad * 2}px`,
    })
    const tipRect = this.tip.getBoundingClientRect()
    const vp = { width: this.win.innerWidth, height: this.win.innerHeight }
    const pos = computeTooltipPosition(
      step.placement,
      { top: r.top, left: r.left, width: r.width, height: r.height },
      { width: tipRect.width || 300, height: tipRect.height || 140 },
      vp,
    )
    this.tip.style.top = `${pos.top}px`
    this.tip.style.left = `${pos.left}px`
  }

  private teardown(): void {
    this.win.removeEventListener('resize', this.reflow)
    this.win.removeEventListener('scroll', this.reflow, true)
    this.root?.remove()
    this.tip?.remove()
    this.root = this.hole = this.tip = null
    this.tour = null
    this.index = 0
  }
}

function el(doc: Document, tag: string, className = ''): HTMLElement {
  const node = doc.createElement(tag)
  if (className) node.className = className
  return node
}
