/**
 * Host-DOM product-tour player (v2).
 *
 * Renders every DAP step type over the real page — tooltip, modal, banner,
 * hotspot, action (guided or "do it for me" driven) and hidden wait steps —
 * resolving each anchored step through the shared `@stept/dom-capture` cascade
 * (see dom-target.ts) so a drifted selector self-heals instead of dead-ending.
 *
 * Everything the player learns is reported back through `onEvent`, including
 * `step_error` with a reason, `step_blocked` when the visitor is shown the
 * explicit can't-find-it card, and `meta.healed` for self-heals. Progress is
 * persisted in localStorage (24h TTL) so a reload, a cross-page step or a new
 * tab resumes mid-tour WITHOUT re-emitting `started` — a duplicated start
 * would silently inflate every completion-rate denominator. A step may carry a
 * `url`: anchored steps navigate there before resolving (persisting progress
 * first, flagged `navigating` so the next load auto-continues), and the last
 * step's Done navigates there after completing.
 *
 * The positioning + eligibility math and the storage codec are exported as pure
 * functions so they can be unit-tested without a browser.
 */

import { renderMarkdown } from './app/md'
import { resolveStepTarget, stepNeedsTarget, waitForTarget, type StepErrorReason } from './dom-target'
import { globMatch } from './loader-core'
import type {
  StepAction,
  StepCta,
  Tour,
  TourEventMeta,
  TourEventName,
  TourSettings,
  TourStep,
} from './types'
import { t } from './i18n'

export type Side = 'top' | 'bottom' | 'left' | 'right'
export type Placement = 'auto' | Side | 'center'

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

/** Do two viewport rects overlap? Touching edges do not count. */
export function rectsIntersect(a: Rect, b: Rect): boolean {
  return (
    a.left < b.left + b.width &&
    b.left < a.left + a.width &&
    a.top < b.top + b.height &&
    b.top < a.top + a.height
  )
}

/**
 * Absolute viewport coords for the tooltip, clamped so it never leaves the
 * screen. `avoid` rects are the widget's OWN surfaces (launcher, tour pill,
 * open messenger panel): a placement that would hide the tooltip under them is
 * traded for the first side that stays clear. When every side collides the
 * requested side wins — a partially covered tooltip beats none at all.
 */
export function computeTooltipPosition(
  placement: Placement,
  target: Rect,
  tip: Size,
  vp: Viewport,
  gap = 12,
  avoid: readonly Rect[] = [],
): { top: number; left: number; side: Side } {
  const requested: Placement = placement === 'center' ? 'auto' : placement
  const first = requested === 'auto' ? resolveAutoPlacement(target, tip, vp, gap) : requested
  const compute = (side: Side): { top: number; left: number; side: Side } => {
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
  const chosen = compute(first)
  if (!avoid.length) return chosen
  const clear = (pos: { top: number; left: number }): boolean =>
    !avoid.some((zone) =>
      rectsIntersect({ top: pos.top, left: pos.left, width: tip.width, height: tip.height }, zone),
    )
  if (clear(chosen)) return chosen
  for (const side of ['bottom', 'top', 'right', 'left'] as const) {
    if (side === first) continue
    const candidate = compute(side)
    if (clear(candidate)) return candidate
  }
  return chosen
}

/**
 * Is `url` (a path or an absolute URL) a different page from `currentHref`?
 * Hash-only differences are the same page — navigating would reload into a
 * loop, since the player checks this again after arriving. Unparseable input
 * answers "same page" for the same reason.
 */
export function isDifferentPage(url: string, currentHref: string): boolean {
  try {
    const target = new URL(url, currentHref)
    const current = new URL(currentHref)
    return (
      target.origin !== current.origin ||
      target.pathname !== current.pathname ||
      target.search !== current.search
    )
  } catch {
    return false
  }
}

/**
 * First tour not already seen locally (client-side dedup for anon visitors).
 * A tour whose server-side frequency is `every_time` bypasses the seen-set —
 * the backend has already decided it should run again.
 */
export function selectFirstEligibleTour(
  tours: readonly Tour[],
  seen: Iterable<string> = [],
): Tour | null {
  const seenSet = seen instanceof Set ? seen : new Set(seen)
  for (const tour of tours) {
    if (!tour.steps.length) continue
    if (tour.frequency_type === 'every_time' || !seenSet.has(tour.id)) return tour
  }
  return null
}

// --- progress persistence (pure, unit-tested) -------------------------------

export interface TourProgress {
  tourId: string
  stepIndex: number
  startedAt: number
  /** Last write. A record older than {@link TOUR_PROGRESS_TTL_MS} is dead. */
  updatedAt: number
  /**
   * Set just before the PLAYER navigates (a cross-page step, a driven navigate
   * action). The next load auto-continues; without the flag — the visitor
   * navigated or closed the tab themselves — the loader offers a resume pill
   * instead of hijacking the page.
   */
  navigating?: boolean
}

/** A half-finished tour stops offering to resume after a day. */
export const TOUR_PROGRESS_TTL_MS = 24 * 60 * 60 * 1000

/** localStorage key holding the in-flight tour position for one widget. */
export function tourProgressKey(widgetKey: string): string {
  return `stept:tour-progress:${widgetKey}`
}

/** Read the persisted position; tolerant of junk, private mode and old shapes.
 * Expired records read as null — yesterday's abandoned tour must not resurrect. */
export function readTourProgress(
  storage: Pick<Storage, 'getItem'> | null,
  key: string,
  now = Date.now(),
): TourProgress | null {
  try {
    const raw = storage?.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<TourProgress>
    if (!parsed || typeof parsed.tourId !== 'string' || typeof parsed.stepIndex !== 'number') {
      return null
    }
    const startedAt = typeof parsed.startedAt === 'number' ? parsed.startedAt : now
    const updatedAt = typeof parsed.updatedAt === 'number' ? parsed.updatedAt : startedAt
    if (now - updatedAt > TOUR_PROGRESS_TTL_MS) return null
    return {
      tourId: parsed.tourId,
      stepIndex: Math.max(0, Math.floor(parsed.stepIndex)),
      startedAt,
      updatedAt,
      ...(parsed.navigating === true ? { navigating: true } : {}),
    }
  } catch {
    return null
  }
}

export function writeTourProgress(
  storage: Pick<Storage, 'setItem'> | null,
  key: string,
  progress: TourProgress,
): void {
  try {
    storage?.setItem(key, JSON.stringify(progress))
  } catch {
    /* private mode — the tour simply restarts after a reload */
  }
}

export function clearTourProgress(
  storage: Pick<Storage, 'removeItem'> | null,
  key: string,
): void {
  try {
    storage?.removeItem(key)
  } catch {
    /* ignore */
  }
}

// --- media URLs (pure, unit-tested) -----------------------------------------

/**
 * Resolve a step media / screenshot URL against the widget's API origin.
 *
 * The backend returns public media as the ROOT-RELATIVE path
 * `/api/widget/media/{workspace_id}/{key}`. That resolves correctly in the
 * dashboard (same origin as the API) but the player runs on the CUSTOMER's
 * domain, where it would 404 — so relative URLs are prefixed with `apiBase`.
 * Anything carrying a scheme (an external CDN, `data:`) is passed through
 * untouched.
 */
export function resolveMediaUrl(url: string, apiBase = ''): string {
  const trimmed = (url ?? '').trim()
  if (!trimmed) return ''
  // scheme-qualified (http:, https:, data:, blob:) or protocol-relative
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed) || trimmed.startsWith('//')) return trimmed
  const base = apiBase.replace(/\/+$/, '')
  if (!base) return trimmed
  return trimmed.startsWith('/') ? `${base}${trimmed}` : `${base}/${trimmed}`
}

/**
 * Foreground that stays readable on `background`, by WCAG relative luminance.
 *
 * Only the DEFAULT: an explicit `text_color` always wins. Non-hex literals
 * (`rebeccapurple`, `rgb(...)`) can't be measured without a layout, so they get
 * white — the same colour the bar has always used.
 */
export function readableOn(background: string): string {
  const hex = (background ?? '').trim().replace('#', '')
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((c) => c + c)
          .join('')
      : hex.slice(0, 6)
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return '#fff'
  const channel = (offset: number): number => {
    const value = parseInt(full.slice(offset, offset + 2), 16) / 255
    return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4)
  }
  const luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
  return luminance > 0.45 ? '#0f172a' : '#fff'
}

/** Same fix for images the editor embedded in a markdown body. */
export function absolutizeMedia(root: ParentNode, apiBase: string): void {
  if (!apiBase) return
  for (const img of root.querySelectorAll('img[src]')) {
    const src = img.getAttribute('src') ?? ''
    const resolved = resolveMediaUrl(src, apiBase)
    if (resolved !== src) img.setAttribute('src', resolved)
  }
}

// --- driven-mode actuation (pure, unit-tested) ------------------------------

/**
 * Set a form control's value the way a user would: through the NATIVE value
 * setter (React & friends patch the instance property and would otherwise miss
 * the change) followed by the `input` + `change` events they listen for.
 */
export function fillElement(el: HTMLElement, value: string): void {
  const view = el.ownerDocument?.defaultView as (Window & typeof globalThis) | null
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
    const ctor =
      tag === 'TEXTAREA'
        ? view?.HTMLTextAreaElement
        : tag === 'SELECT'
          ? view?.HTMLSelectElement
          : view?.HTMLInputElement
    const descriptor = ctor ? Object.getOwnPropertyDescriptor(ctor.prototype, 'value') : undefined
    if (descriptor?.set) descriptor.set.call(el, value)
    else (el as HTMLInputElement).value = value
  } else {
    el.textContent = value
  }
  const EventCtor = view?.Event ?? Event
  el.dispatchEvent(new EventCtor('input', { bubbles: true }))
  el.dispatchEvent(new EventCtor('change', { bubbles: true }))
}

/** Perform one driven action step. Returns false when it could not be done. */
export function performAction(
  action: StepAction,
  el: HTMLElement | null,
  win: Pick<Window, 'location'>,
): boolean {
  switch (action.kind) {
    case 'click':
      if (!el) return false
      el.click()
      return true
    case 'fill':
      if (!el) return false
      fillElement(el, action.value ?? '')
      return true
    case 'navigate':
      if (!action.url) return false
      win.location.assign(action.url)
      return true
    default:
      return false
  }
}

// --- the DOM player ---------------------------------------------------------

export interface TourPlayerOptions {
  accent?: string
  /** Reports started / step_viewed / step_blocked / completed / dismissed / step_error. */
  onEvent?: (event: TourEventName, stepIndex: number | null, meta?: TourEventMeta) => void
  /** Injected for tests; defaults to the real globals. */
  doc?: Document
  win?: Window & typeof globalThis
  /** Widget API origin — root-relative step media resolves against it. */
  apiBase?: string
  /** localStorage key for resume-after-reload (see {@link tourProgressKey}). */
  progressKey?: string
  storage?: Storage | null
  /** Render a "Preview" badge and never persist progress. */
  preview?: boolean
  /** How long an anchored step waits for its element before it is blocked. */
  resolveTimeoutMs?: number
  /** Driven mode: highlight dwell before the action is performed. */
  actionDelayMs?: number
  /**
   * Viewport rects of the widget's own chrome (launcher, tour pill, open
   * messenger panel). Tooltip placement treats them as exclusion zones.
   */
  getObstructions?: () => Rect[]
}

const DEFAULT_SETTINGS: TourSettings = {
  mode: 'guided',
  backdrop: true,
  show_progress: true,
  dismissable: true,
}

const STYLE_ID = 'stept-tour-style'
const CSS = `
.stept-tour-root{position:fixed;inset:0;z-index:2147483000;pointer-events:none}
.stept-tour-root.stept-veil{pointer-events:auto;background:rgba(15,23,42,.55)}
.stept-tour-hole{position:fixed;z-index:2147483000;border-radius:8px;
  box-shadow:0 0 0 9999px rgba(15,23,42,.55);transition:all .18s ease;pointer-events:none;
  outline:2px solid var(--stept-accent,#5b46e5);outline-offset:2px}
.stept-tour-hole.stept-nodim{box-shadow:none}
.stept-tour-hole[hidden]{display:none}
/* Four transparent panels around the spotlight cutout. The dim is painted by
   the hole's box-shadow; these only CATCH pointer events, so the page under the
   backdrop is inert while the taught element itself stays clickable. */
.stept-tour-blocker{position:fixed;z-index:2147483000;pointer-events:auto}
.stept-tour-blocker[hidden]{display:none}
.stept-tour-tip{position:fixed;z-index:2147483001;max-width:340px;width:calc(100vw - 32px);
  background:#fff;color:#0f172a;border-radius:12px;box-shadow:0 12px 40px rgba(15,23,42,.28);
  padding:16px 16px 12px;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  box-sizing:border-box;outline:none}
.stept-tour-tip[hidden]{display:none}
/* Programmatic focus (every step) stays quiet; only keyboard focus rings. */
.stept-tour-tip:focus-visible{box-shadow:0 12px 40px rgba(15,23,42,.28),
  0 0 0 2px var(--stept-accent,#5b46e5)}
.stept-tour-btn:focus-visible,.stept-tour-close:focus-visible{
  outline:2px solid var(--stept-accent,#5b46e5);outline-offset:2px;border-radius:8px}
.stept-tour-tip.stept-centered{top:50%;left:50%;transform:translate(-50%,-50%);max-width:420px}
.stept-tour-tip h4{margin:0 0 6px;font-size:15px;font-weight:600}
.stept-tour-tip p{margin:0 0 8px;color:#334155}
.stept-tour-body{margin:0 0 10px;color:#334155}
.stept-tour-body :first-child{margin-top:0}
.stept-tour-body :last-child{margin-bottom:0}
.stept-tour-body p{margin:0 0 8px}
.stept-tour-body ul,.stept-tour-body ol{margin:0 0 8px;padding-left:20px}
.stept-tour-body code{background:rgba(100,116,139,.16);border-radius:4px;padding:1px 4px;font-size:12px}
.stept-tour-body img{max-width:100%;height:auto;border-radius:8px}
.stept-tour-body table{border-collapse:collapse;width:100%;font-size:13px}
.stept-tour-body th,.stept-tour-body td{border:1px solid rgba(100,116,139,.3);padding:4px 6px;text-align:left}
.stept-tour-media{display:block;width:100%;max-height:180px;object-fit:cover;border-radius:8px;margin:0 0 10px}
.stept-tour-foot{display:flex;align-items:center;justify-content:space-between;gap:8px}
.stept-tour-count{font-size:12px;color:#64748b}
.stept-tour-hint{font-size:12px;color:#64748b;margin:0}
.stept-tour-wait{display:flex;align-items:center;gap:8px;margin:0;color:#64748b;font-size:13px}
.stept-tour-wait i{flex:none;width:12px;height:12px;border-radius:50%;
  border:2px solid rgba(100,116,139,.3);border-top-color:var(--stept-accent,#5b46e5);
  animation:stept-tour-spin .7s linear infinite}
@keyframes stept-tour-spin{to{transform:rotate(360deg)}}
.stept-tour-actions{display:flex;gap:8px}
.stept-tour-bar{height:3px;border-radius:2px;background:rgba(100,116,139,.22);margin:0 0 10px;overflow:hidden}
.stept-tour-bar i{display:block;height:100%;background:var(--stept-accent,#5b46e5);transition:width .2s ease}
.stept-tour-btn{border:0;border-radius:8px;padding:7px 14px;font-size:13px;font-weight:600;cursor:pointer;
  font-family:inherit}
.stept-tour-btn.primary{background:var(--stept-accent,#5b46e5);color:#fff}
.stept-tour-btn.ghost{background:transparent;color:#475569}
.stept-tour-close{position:absolute;top:8px;right:8px;border:0;background:transparent;
  font-size:16px;line-height:1;cursor:pointer;color:#94a3b8;padding:4px}
.stept-tour-badge{display:inline-block;margin:0 0 6px;padding:1px 7px;border-radius:99px;font-size:10px;
  font-weight:700;letter-spacing:.04em;text-transform:uppercase;background:var(--stept-accent,#5b46e5);color:#fff}
.stept-tour-banner{position:fixed;left:0;right:0;z-index:2147483001;pointer-events:auto;
  display:flex;align-items:center;gap:12px;padding:12px 16px;box-sizing:border-box;
  background:var(--stept-banner-bg,var(--stept-accent,#5b46e5));color:var(--stept-banner-fg,#fff);
  font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  box-shadow:0 6px 24px rgba(15,23,42,.22)}
.stept-tour-banner[hidden]{display:none}
.stept-tour-banner.stept-top{top:0}
.stept-tour-banner.stept-bottom{bottom:0}
/* Inline: the bar is in flow at the top/bottom of the body, so the page moves
   instead of being covered. position:static is what does the work — it drops
   the fixed offsets set above. */
.stept-tour-banner.stept-inline{position:static;box-shadow:none}
/* Narrower than the viewport: centre the bar and round it off the edge. */
.stept-tour-banner.stept-boxed{left:50%;right:auto;transform:translateX(-50%);
  max-width:calc(100vw - 24px);border-radius:10px}
.stept-tour-banner.stept-boxed.stept-top{top:12px}
.stept-tour-banner.stept-boxed.stept-bottom{bottom:12px}
.stept-tour-banner.stept-inline.stept-boxed{transform:none;margin:12px auto}
.stept-tour-banner.stept-rounded{border-radius:10px}
.stept-tour-banner.stept-center{justify-content:center;text-align:center}
.stept-tour-banner.stept-center .stept-tour-banner-text{flex:0 1 auto}
.stept-tour-banner .stept-tour-banner-icon{flex:none;font-size:18px;line-height:1}
.stept-tour-banner .stept-tour-banner-text{flex:1;min-width:0}
.stept-tour-banner strong{display:block;font-size:14px}
.stept-tour-banner .stept-tour-body{color:inherit;opacity:.92;margin:0}
.stept-tour-banner .stept-tour-btn{flex:none}
.stept-tour-banner .stept-tour-btn.primary{background:var(--stept-banner-fg,#fff);
  color:var(--stept-banner-bg,#0f172a)}
.stept-tour-banner .stept-tour-btn.ghost{background:transparent;color:inherit;
  border:1px solid currentColor;opacity:.85}
.stept-tour-banner .stept-tour-close{position:static;color:inherit;opacity:.85}
.stept-tour-beacon{position:fixed;z-index:2147483001;width:18px;height:18px;border-radius:50%;
  background:var(--stept-accent,#5b46e5);border:0;padding:0;cursor:pointer;pointer-events:auto;
  box-shadow:0 0 0 4px rgba(99,102,241,.35);animation:stept-tour-pulse 1.8s ease-out infinite}
.stept-tour-beacon[hidden]{display:none}
@keyframes stept-tour-pulse{
  0%{box-shadow:0 0 0 0 rgba(99,102,241,.55)}
  70%{box-shadow:0 0 0 14px rgba(99,102,241,0)}
  100%{box-shadow:0 0 0 0 rgba(99,102,241,0)}
}
.stept-tour-acting{outline:3px solid var(--stept-accent,#5b46e5) !important;outline-offset:2px;
  animation:stept-tour-pulse .6s ease-out 1}
@media (prefers-color-scheme:dark){
  .stept-tour-tip{background:#1e293b;color:#f1f5f9}
  .stept-tour-tip p,.stept-tour-body{color:#cbd5e1}
  .stept-tour-btn.ghost{color:#cbd5e1}
}
@media (prefers-reduced-motion:reduce){
  .stept-tour-beacon,.stept-tour-acting,.stept-tour-wait i{animation:none}
}
`

export class TourPlayer {
  private doc: Document
  private win: Window & typeof globalThis
  private accent: string
  private onEvent: (event: TourEventName, stepIndex: number | null, meta?: TourEventMeta) => void
  private storage: Storage | null
  private progressKey: string
  private apiBase: string
  private preview: boolean
  private resolveTimeoutMs: number
  private actionDelayMs: number

  private tour: Tour | null = null
  private settings: TourSettings = DEFAULT_SETTINGS
  private index = 0
  private startedAt = 0
  private runToken = 0

  private root: HTMLElement | null = null
  private hole: HTMLElement | null = null
  private tip: HTMLElement | null = null
  private banner: HTMLElement | null = null
  private beacon: HTMLElement | null = null
  /** Pointer-catching panels around the spotlight cutout (top/bottom/left/right). */
  private blockers: HTMLElement[] = []

  private target: HTMLElement | null = null
  private acting: HTMLElement | null = null
  private stepCleanups: Array<() => void> = []
  private resizeObserver: ResizeObserver | null = null
  private previousFocus: Element | null = null
  private rafPending = false

  private reflow = (): void => this.schedulePosition()
  private onKeyDown = (event: KeyboardEvent): void => this.handleKey(event)

  private getObstructions: (() => Rect[]) | null

  constructor(opts: TourPlayerOptions = {}) {
    this.doc = opts.doc ?? document
    this.win = opts.win ?? (this.doc.defaultView as Window & typeof globalThis) ?? window
    this.accent = opts.accent ?? '#5b46e5'
    this.onEvent = opts.onEvent ?? (() => {})
    this.progressKey = opts.progressKey ?? 'stept:tour-progress'
    this.apiBase = opts.apiBase ?? ''
    // localStorage, not sessionStorage: progress must survive a cross-page
    // step, a reload AND a new tab (where it becomes a resume offer).
    this.storage = opts.storage !== undefined ? opts.storage : safeLocalStorage(this.win)
    this.preview = opts.preview ?? false
    this.resolveTimeoutMs = opts.resolveTimeoutMs ?? 3000
    this.actionDelayMs = opts.actionDelayMs ?? 600
    this.getObstructions = opts.getObstructions ?? null
  }

  get active(): boolean {
    return this.tour !== null
  }

  get activeTourId(): string | null {
    return this.tour?.id ?? null
  }

  get stepIndex(): number {
    return this.index
  }

  /** Start (or resume) a tour. Resuming never re-emits `started`. */
  start(tour: Tour, opts: { preview?: boolean } = {}): void {
    if (this.tour) this.teardown()
    if (!tour.steps.length) return
    this.tour = tour
    this.preview = opts.preview ?? this.preview
    this.settings = { ...DEFAULT_SETTINGS, ...(tour.settings ?? {}) }
    this.accent = tour.theme?.accent || this.accent
    this.previousFocus = this.doc.activeElement

    // Any stored position for THIS tour means it already started in this
    // session (progress is cleared on completed/dismissed), so resuming must
    // not re-emit `started` — that is what inflated completion-rate
    // denominators on every reload.
    const saved = this.preview ? null : readTourProgress(this.storage, this.progressKey)
    const resume =
      saved && saved.tourId === tour.id && saved.stepIndex < tour.steps.length ? saved : null
    this.startedAt = resume ? resume.startedAt : Date.now()

    this.ensureStyle()
    this.build()
    this.win.addEventListener('resize', this.reflow)
    this.win.addEventListener('scroll', this.reflow, true)
    this.doc.addEventListener('keydown', this.onKeyDown, true)

    if (!resume) this.emit('started', null)
    this.showStep(resume ? resume.stepIndex : 0)
  }

  next(): void {
    if (!this.tour) return
    const step = this.tour.steps[this.index]
    if (step && this.index === this.tour.steps.length - 1) {
      // Every way of leaving the last step (Done, arrow key, element click)
      // funnels through here, so the step's destination url is always honoured.
      this.completeFrom(step)
      return
    }
    this.goto(this.index + 1)
  }

  back(): void {
    if (!this.tour) return
    if (this.index > 0) this.showStep(this.index - 1)
  }

  /** `neverAgain` marks the dismissal as final, whatever the frequency rule. */
  dismiss(neverAgain = false): void {
    this.finish('dismissed', neverAgain ? { never_again: true } : undefined)
  }

  /** Tear the player down without reporting anything (e.g. loader shutdown). */
  stop(): void {
    this.teardown()
  }

  // --- flow ----------------------------------------------------------------

  private goto(index: number): void {
    if (!this.tour) return
    if (index >= this.tour.steps.length) this.finish('completed')
    else this.showStep(index)
  }

  private finish(event: 'completed' | 'dismissed', extra?: TourEventMeta): void {
    if (!this.tour) return
    this.emit(event, event === 'dismissed' ? this.index : null, extra)
    this.clearProgress()
    this.teardown()
  }

  /** Finish from the last step, honouring its destination url (final CTA). */
  private completeFrom(step: TourStep): void {
    const url = (step.url ?? '').trim()
    const navigate = Boolean(url) && !this.preview && isDifferentPage(url, this.win.location.href)
    // Finish FIRST: progress is cleared before the navigation, so the
    // destination page cannot resurrect the tour that was just completed.
    this.finish('completed')
    if (navigate) this.win.location.assign(url)
  }

  private showStep(i: number): void {
    if (!this.tour) return
    const step = this.tour.steps[i]
    if (!step) {
      this.finish('completed')
      return
    }
    const token = ++this.runToken
    this.resetStep()
    this.index = i

    if ((step.type ?? 'tooltip') === 'wait') {
      this.persist(i)
      this.emit('step_viewed', i)
      void this.runWaitStep(step, i, token)
      return
    }

    if (!stepNeedsTarget(step)) {
      this.persist(i)
      this.present(step, i, null, false)
      return
    }

    const immediate = resolveStepTarget(step, this.doc)
    if (immediate.el) {
      this.persist(i)
      this.present(step, i, immediate.el, immediate.healed)
      return
    }
    // The anchor is not here. A step that names its page is navigated to —
    // progress goes down FIRST (flagged `navigating`) so the next load
    // auto-continues at this exact step instead of restarting the tour.
    if (this.navigateToStep(step, i)) return
    this.persist(i)
    if (immediate.reason === 'in_iframe') {
      // Cross-frame steps can never resolve in the top document — the old
      // report-and-skip behaviour stays the honest one.
      this.failStep(i, 'in_iframe')
      return
    }
    if (this.resolveTimeoutMs <= 0) {
      this.blockStep(step, i, 'not_found')
      return
    }
    this.renderSearching(step, i)
    void this.awaitTarget(step, i, token)
  }

  /** Cross-page step: persist, then leave. True when navigation started. */
  private navigateToStep(step: TourStep, i: number): boolean {
    const url = (step.url ?? '').trim()
    if (!url || this.preview) return false
    if (!isDifferentPage(url, this.win.location.href)) return false
    this.persist(i, { navigating: true })
    this.win.location.assign(url)
    return true
  }

  /** SPA render race: give the element a moment to appear before blocking. */
  private async awaitTarget(step: TourStep, i: number, token: number): Promise<void> {
    const result = await waitForTarget(step, this.resolveTimeoutMs, {
      doc: this.doc,
      win: this.win,
    })
    if (token !== this.runToken || !this.tour) return
    if (!result.el) {
      if (result.reason === 'in_iframe') this.failStep(i, 'in_iframe')
      else this.blockStep(step, i, result.reason ?? 'timeout')
      return
    }
    this.present(step, i, result.el, result.healed)
  }

  private failStep(i: number, reason: StepErrorReason): void {
    this.emit('step_error', i, { reason })
    this.goto(i + 1)
  }

  /**
   * The anchor is genuinely not on this page: say so ON the card and hand the
   * choice to the visitor. Auto-skipping here is what made Next feel dead —
   * the tour silently burned through its remaining steps.
   */
  private blockStep(step: TourStep, i: number, reason: StepErrorReason): void {
    this.emit('step_blocked', i, { reason })
    this.renderBlocked(step, i)
  }

  private present(step: TourStep, i: number, el: HTMLElement | null, healed: boolean): void {
    this.target = el
    const type = step.type ?? 'tooltip'
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
    }
    if (type === 'banner') this.renderBanner(step, i)
    else if (type === 'hotspot') this.renderHotspot(step, i, el)
    else this.renderTip(step, i, el, type === 'modal')

    this.position()
    this.observeTarget(el)
    this.emit('step_viewed', i, healed ? { healed: true } : undefined)

    if (type === 'action' && this.settings.mode === 'driven' && step.action) {
      this.runDrivenAction(step.action, el, i)
      return
    }
    if (type === 'hotspot') return // advance binds when the beacon opens the tip
    this.bindAdvance(step, el)
    this.focusTip()
  }

  // --- step kinds ----------------------------------------------------------

  private renderTip(step: TourStep, i: number, el: HTMLElement | null, centered: boolean): void {
    const tip = this.tip
    if (!tip || !this.tour) return
    const type = step.type ?? 'tooltip'
    const guidedAction = type === 'action' && this.settings.mode !== 'driven'
    tip.innerHTML = ''
    tip.hidden = false
    tip.classList.toggle('stept-centered', centered)
    if (centered) {
      // Inline coords left over from an anchored step outrank the class
      // (inline > class), which pinned the "centred" modal at the previous
      // tooltip's corner. Centring must always drop them.
      tip.style.top = ''
      tip.style.left = ''
    }
    tip.setAttribute('aria-modal', this.settings.backdrop && centered ? 'true' : 'false')
    if (this.banner) this.banner.hidden = true
    if (this.beacon) this.beacon.hidden = true

    if (this.hole) {
      this.hole.hidden = !el
      this.hole.classList.toggle('stept-nodim', !this.settings.backdrop)
    }
    this.root?.classList.toggle('stept-veil', centered && this.settings.backdrop)
    // Spotlight backdrop: block the page around the cutout, never the cutout.
    this.setBlockers(Boolean(el) && this.settings.backdrop && !centered)

    if (this.preview) {
      const badge = el2(this.doc, 'span', 'stept-tour-badge')
      badge.textContent = t('tour.preview')
      tip.appendChild(badge)
    }
    if (this.settings.dismissable) tip.appendChild(this.closeButton())
    if (step.media) tip.appendChild(this.mediaNode(step.media))

    const titleId = `stept-tour-title-${i}`
    const heading = el2(this.doc, 'h4')
    heading.id = titleId
    heading.textContent = step.title || defaultTitle(step, i)
    tip.appendChild(heading)
    tip.setAttribute('aria-labelledby', titleId)

    const body = step.body || (guidedAction ? actionInstruction(step) : '')
    if (body) {
      const node = el2(this.doc, 'div', 'stept-tour-body')
      node.innerHTML = renderMarkdown(body)
      absolutizeMedia(node, this.apiBase)
      tip.appendChild(node)
    }
    if (this.settings.show_progress) tip.appendChild(this.progressBar(i))
    tip.appendChild(this.footer(step, i, guidedAction))
  }

  /** Bare centred card shared by the searching and blocked states. */
  private renderStateCard(step: TourStep, i: number): HTMLElement | null {
    const tip = this.tip
    if (!tip || !this.tour) return null
    this.hideChrome()
    tip.hidden = false
    tip.innerHTML = ''
    tip.classList.add('stept-centered')
    tip.style.top = ''
    tip.style.left = ''
    tip.setAttribute('aria-modal', 'false')
    if (this.settings.dismissable) tip.appendChild(this.closeButton())
    const heading = el2(this.doc, 'h4')
    heading.id = `stept-tour-title-${i}`
    heading.textContent = step.title || defaultTitle(step, i)
    tip.appendChild(heading)
    tip.setAttribute('aria-labelledby', heading.id)
    return tip
  }

  /** Subtle holding state while {@link awaitTarget} chases a hydrating anchor. */
  private renderSearching(step: TourStep, i: number): void {
    const tip = this.renderStateCard(step, i)
    if (!tip) return
    const wait = el2(this.doc, 'p', 'stept-tour-wait')
    wait.appendChild(el2(this.doc, 'i'))
    wait.appendChild(this.doc.createTextNode('Finding it on this page…'))
    tip.appendChild(wait)
  }

  /** The explicit blocked card: never a dead Next, never a silent skip. */
  private renderBlocked(step: TourStep, i: number): void {
    const tip = this.renderStateCard(step, i)
    if (!tip || !this.tour) return
    const body = el2(this.doc, 'p')
    body.textContent = "Can't find this element on this page"
    tip.appendChild(body)
    if (this.settings.show_progress) tip.appendChild(this.progressBar(i))

    const foot = el2(this.doc, 'div', 'stept-tour-foot')
    const left = el2(this.doc, 'span', 'stept-tour-count')
    if (this.settings.show_progress) left.textContent = `${i + 1} of ${this.tour.steps.length}`
    foot.appendChild(left)

    const actions = el2(this.doc, 'div', 'stept-tour-actions')
    const skip = el2(this.doc, 'button', 'stept-tour-btn ghost')
    skip.textContent = 'Skip step'
    skip.onclick = () => this.goto(i + 1)
    actions.appendChild(skip)
    // Always offered, even when `dismissable` is off — a blocked step with no
    // way out would trap the visitor on a card about a missing element.
    const end = el2(this.doc, 'button', 'stept-tour-btn primary')
    end.textContent = 'End tour'
    end.onclick = () => this.dismiss()
    actions.appendChild(end)
    foot.appendChild(actions)
    tip.appendChild(foot)
    this.focusTip()
  }

  private renderHotspot(step: TourStep, i: number, el: HTMLElement | null): void {
    if (this.tip) this.tip.hidden = true
    if (this.banner) this.banner.hidden = true
    if (this.hole) this.hole.hidden = true
    this.root?.classList.remove('stept-veil')
    this.setBlockers(false)
    const beacon = this.beacon
    if (!beacon) return
    beacon.hidden = false
    beacon.setAttribute('aria-label', step.title || t('tour.show_tip'))
    const open = (): void => {
      this.renderTip(step, i, el, false)
      this.position()
      this.bindAdvance(step, el)
      this.focusTip()
    }
    beacon.onclick = open
  }

  private renderBanner(step: TourStep, i: number): void {
    const banner = this.banner
    if (!banner || !this.tour) return
    if (this.tip) this.tip.hidden = true
    if (this.beacon) this.beacon.hidden = true
    if (this.hole) this.hole.hidden = true
    // A banner is an announcement, never a modal: the page stays fully usable.
    this.root?.classList.remove('stept-veil')
    this.setBlockers(false)

    const theme = this.tour.theme?.banner ?? {}
    const position = this.tour.theme?.position === 'top' ? 'top' : 'bottom'
    const fullWidth = theme.full_width !== false
    const inline = theme.layout === 'inline'

    banner.className = [
      'stept-tour-banner',
      `stept-${position}`,
      inline ? 'stept-inline' : '',
      !fullWidth ? 'stept-boxed' : '',
      fullWidth && theme.rounded ? 'stept-rounded' : '',
      theme.align === 'center' ? 'stept-center' : '',
    ]
      .filter(Boolean)
      .join(' ')

    // Colours are author-supplied CSS literals, validated server-side against a
    // colour grammar; `setProperty` keeps them inside their own declaration.
    const background = theme.background || this.accent
    banner.style.setProperty('--stept-banner-bg', background)
    banner.style.setProperty('--stept-banner-fg', theme.text_color || readableOn(background))
    banner.style.width = !fullWidth && theme.max_width ? `${theme.max_width}px` : ''

    // Inline layout re-parents the bar into the document flow. Re-homing on
    // every render is intentional: the author can flip `layout` between saves
    // and a live-updating preview must follow without a reload.
    this.mountBanner(banner, inline, position)

    banner.hidden = false
    banner.innerHTML = ''

    if (theme.icon) {
      const icon = el2(this.doc, 'span', 'stept-tour-banner-icon')
      icon.textContent = theme.icon
      icon.setAttribute('aria-hidden', 'true')
      banner.appendChild(icon)
    }

    const text = el2(this.doc, 'div', 'stept-tour-banner-text')
    if (step.title) {
      const strong = el2(this.doc, 'strong')
      strong.id = `stept-tour-title-${i}`
      strong.textContent = step.title
      text.appendChild(strong)
      banner.setAttribute('aria-labelledby', strong.id)
    }
    if (step.body) {
      const body = el2(this.doc, 'div', 'stept-tour-body')
      body.innerHTML = renderMarkdown(step.body)
      absolutizeMedia(body, this.apiBase)
      text.appendChild(body)
    }
    banner.appendChild(text)

    if (step.secondary_cta?.label) {
      banner.appendChild(this.ctaButton(step.secondary_cta, 'ghost', i))
    }
    const isLast = i === this.tour.steps.length - 1
    banner.appendChild(
      this.ctaButton(step.cta ?? null, 'primary', i, isLast ? t('tour.got_it') : t('tour.next')),
    )
    if (this.settings.dismissable) {
      banner.appendChild(this.closeButton(theme.dismiss === 'never_again'))
    }
  }

  /**
   * Put the bar where the layout demands: in `<body>` flow for `inline`
   * (first or last child, so it pushes rather than covers), back in the
   * fixed-position overlay root otherwise.
   */
  private mountBanner(banner: HTMLElement, inline: boolean, position: 'top' | 'bottom'): void {
    const body = this.doc.body
    if (!inline) {
      if (this.root && banner.parentNode !== this.root) this.root.appendChild(banner)
      return
    }
    if (position === 'top') {
      if (body.firstChild !== banner) body.insertBefore(banner, body.firstChild)
    } else if (body.lastChild !== banner) {
      body.appendChild(banner)
    }
  }

  /** A step button: author copy when given, otherwise the player's default. */
  private ctaButton(
    cta: StepCta | null,
    variant: 'primary' | 'ghost',
    index: number,
    fallbackLabel = '',
  ): HTMLElement {
    const button = el2(this.doc, 'button', `stept-tour-btn ${variant}`)
    button.textContent = cta?.label?.trim() || fallbackLabel
    const url = cta?.url?.trim()
    button.onclick = () => {
      if (url) {
        // `noopener` is not optional: without it the opened page can reach back
        // through `window.opener` and navigate the customer's app.
        this.win.open(url, '_blank', 'noopener,noreferrer')
        this.emit('step_viewed', index, { cta: cta?.label || url })
        return
      }
      this.next()
    }
    return button
  }

  private runDrivenAction(action: StepAction, el: HTMLElement | null, i: number): void {
    if (el) {
      el.classList.add('stept-tour-acting')
      this.acting = el
    }
    const token = this.runToken
    const timer = this.win.setTimeout(() => {
      if (token !== this.runToken || !this.tour) return
      this.clearActing()
      // A navigate action unloads the page: persist the NEXT step first —
      // flagged `navigating`, so the reload auto-continues the tour instead of
      // restarting (or merely offering) it.
      if (action.kind === 'navigate') this.persist(i + 1, { navigating: true })
      let ok = false
      try {
        ok = performAction(action, el, this.win)
      } catch {
        ok = false
      }
      if (!ok) {
        this.emit('step_error', i, { reason: 'action_failed' })
      }
      this.goto(i + 1)
    }, this.actionDelayMs)
    this.stepCleanups.push(() => this.win.clearTimeout(timer))
  }

  private async runWaitStep(step: TourStep, i: number, token: number): Promise<void> {
    this.hideChrome()
    const config = step.wait ?? { for: 'element' as const, timeout_ms: 10_000 }
    const timeout = config.timeout_ms ?? 10_000
    let ok: boolean
    let reason: StepErrorReason = 'timeout'
    if ((config.for ?? 'element') === 'url') {
      ok = await this.waitForUrl(config.url_pattern ?? '', timeout)
    } else {
      const result = await waitForTarget(step, timeout, { doc: this.doc, win: this.win })
      ok = result.el !== null
      if (result.reason === 'in_iframe') reason = 'in_iframe'
    }
    if (token !== this.runToken || !this.tour) return
    if (!ok) this.emit('step_error', i, { reason })
    this.goto(i + 1)
  }

  /** Resolve true once `location.href` matches, false when the budget expires. */
  private waitForUrl(pattern: string, timeoutMs: number): Promise<boolean> {
    const matches = (): boolean => !pattern || globMatch(pattern, this.win.location.href)
    if (matches()) return Promise.resolve(true)
    return new Promise<boolean>((resolve) => {
      let done = false
      const finish = (ok: boolean): void => {
        if (done) return
        done = true
        this.win.clearInterval(poll)
        this.win.clearTimeout(timer)
        this.win.removeEventListener('popstate', check)
        this.win.removeEventListener('stept:locationchange', check)
        resolve(ok)
      }
      const check = (): void => {
        if (matches()) finish(true)
      }
      const poll = this.win.setInterval(check, 200)
      const timer = this.win.setTimeout(() => finish(false), timeoutMs)
      this.win.addEventListener('popstate', check)
      this.win.addEventListener('stept:locationchange', check)
      this.stepCleanups.push(() => finish(false))
    })
  }

  // --- chrome --------------------------------------------------------------

  /** `permanent` reports `meta.never_again`, which the backend honours over the
   * tour's own frequency rule — the difference between "not now" and "stop". */
  private closeButton(permanent = false): HTMLElement {
    const close = el2(this.doc, 'button', 'stept-tour-close')
    close.textContent = '×'
    close.setAttribute('aria-label', permanent ? t('tour.dismiss_forever') : t('tour.dismiss'))
    close.onclick = () => this.dismiss(permanent)
    return close
  }

  private mediaNode(media: { type: 'image' | 'video'; url: string }): HTMLElement {
    // Backend media is root-relative; the player runs on the customer's origin.
    const src = resolveMediaUrl(media.url, this.apiBase)
    if (media.type === 'video') {
      const video = this.doc.createElement('video')
      video.className = 'stept-tour-media'
      video.src = src
      video.controls = true
      video.playsInline = true
      return video
    }
    const img = this.doc.createElement('img')
    img.className = 'stept-tour-media'
    img.src = src
    img.alt = ''
    return img
  }

  private progressBar(i: number): HTMLElement {
    const total = this.tour?.steps.length ?? 1
    const bar = el2(this.doc, 'div', 'stept-tour-bar')
    const fill = el2(this.doc, 'i')
    fill.style.width = `${Math.round(((i + 1) / total) * 100)}%`
    bar.appendChild(fill)
    return bar
  }

  private footer(step: TourStep, i: number, guidedAction: boolean): HTMLElement {
    const total = this.tour?.steps.length ?? 1
    const isLast = i === total - 1
    const advance = step.advance?.on ?? 'button'
    const clickAdvance = advance === 'element_click' || step.advance_on_click === true
    const foot = el2(this.doc, 'div', 'stept-tour-foot')

    const left = el2(this.doc, 'span', 'stept-tour-count')
    if (this.settings.show_progress) left.textContent = `${i + 1} of ${total}`
    foot.appendChild(left)

    const actions = el2(this.doc, 'div', 'stept-tour-actions')
    if (i > 0) {
      const back = el2(this.doc, 'button', 'stept-tour-btn ghost')
      back.textContent = t('tour.back')
      back.onclick = () => this.back()
      actions.appendChild(back)
    }
    // element_click / advance_on_click / input / delay steps advance from the
    // page itself; showing a Next button there would let the user skip the
    // thing being taught.
    const selfAdvancing = (guidedAction || clickAdvance || advance !== 'button') && !isLast
    if (selfAdvancing) {
      const hint = el2(this.doc, 'p', 'stept-tour-hint')
      hint.textContent = guidedAction
        ? actionHint(step)
        : advance === 'input'
          ? t('tour.fill_field')
          : advance === 'delay' && !clickAdvance
            ? 'Continuing…'
            : t('tour.click_target_continue')
      actions.appendChild(hint)
    } else {
      const nextBtn = el2(this.doc, 'button', 'stept-tour-btn primary')
      nextBtn.textContent = isLast ? t('tour.done') : t('tour.next')
      nextBtn.onclick = () => this.next()
      actions.appendChild(nextBtn)
    }
    foot.appendChild(actions)
    return foot
  }

  private bindAdvance(step: TourStep, el: HTMLElement | null): void {
    const advance = step.advance ?? { on: 'button' as const }
    const guidedAction = (step.type ?? 'tooltip') === 'action' && this.settings.mode !== 'driven'
    // `advance_on_click` is the recorder's per-step opt-in for interact-to-
    // advance; the cutout is click-through, so the host handler fires too.
    const mode =
      guidedAction || step.advance_on_click === true ? 'element_click' : advance.on

    if (mode === 'element_click' && el) {
      const handler = (): void => this.next()
      el.addEventListener('click', handler, true)
      this.stepCleanups.push(() => el.removeEventListener('click', handler, true))
      return
    }
    if (mode === 'input' && el) {
      const handler = (event: Event): void => {
        if (event.type === 'keydown' && (event as KeyboardEvent).key !== 'Enter') return
        const value = (el as HTMLInputElement).value
        if (typeof value === 'string' && !value.trim()) return
        this.next()
      }
      for (const type of ['change', 'blur', 'keydown']) {
        el.addEventListener(type, handler, true)
        this.stepCleanups.push(() => el.removeEventListener(type, handler, true))
      }
      return
    }
    if (mode === 'delay') {
      const delay = Math.max(100, advance.delay_ms ?? 3000)
      const timer = this.win.setTimeout(() => this.next(), delay)
      this.stepCleanups.push(() => this.win.clearTimeout(timer))
    }
  }

  private handleKey(event: KeyboardEvent): void {
    if (!this.tour) return
    if (event.key === 'Escape' && this.settings.dismissable) {
      // Fully consumed: the host app must not ALSO close its own dialog on the
      // Escape that dismissed the tour (we listen in the capture phase).
      event.preventDefault()
      event.stopPropagation()
      this.dismiss()
      return
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      this.next()
      return
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      this.back()
    }
  }

  private focusTip(): void {
    const tip = this.tip
    if (!tip || tip.hidden || typeof tip.focus !== 'function') return
    try {
      tip.focus({ preventScroll: true })
    } catch {
      tip.focus()
    }
  }

  private hideChrome(): void {
    if (this.tip) this.tip.hidden = true
    if (this.banner) this.banner.hidden = true
    if (this.beacon) this.beacon.hidden = true
    if (this.hole) this.hole.hidden = true
    this.root?.classList.remove('stept-veil')
    this.setBlockers(false)
  }

  private setBlockers(active: boolean): void {
    for (const blocker of this.blockers) blocker.hidden = !active
  }

  // --- geometry ------------------------------------------------------------

  private observeTarget(el: HTMLElement | null): void {
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    const Observer = (this.win as unknown as { ResizeObserver?: typeof ResizeObserver })
      .ResizeObserver
    if (!el || typeof Observer !== 'function') return
    const observer = new Observer(this.reflow)
    observer.observe(el)
    this.resizeObserver = observer
  }

  private schedulePosition(): void {
    if (this.rafPending) return
    const raf = this.win.requestAnimationFrame?.bind(this.win)
    if (!raf) {
      this.position()
      return
    }
    this.rafPending = true
    raf(() => {
      this.rafPending = false
      this.position()
    })
  }

  private position(): void {
    if (!this.tour) return
    const step = this.tour.steps[this.index]
    if (!step) return
    const el = this.target
    const tip = this.tip
    if (el && this.hole && !this.hole.hidden) {
      const r = el.getBoundingClientRect()
      const pad = 6
      Object.assign(this.hole.style, {
        top: `${r.top - pad}px`,
        left: `${r.left - pad}px`,
        width: `${r.width + pad * 2}px`,
        height: `${r.height + pad * 2}px`,
      })
      if (this.blockers.length && !this.blockers[0]!.hidden) {
        this.positionBlockers({
          top: r.top - pad,
          left: r.left - pad,
          width: r.width + pad * 2,
          height: r.height + pad * 2,
        })
      }
    }
    if (el && this.beacon && !this.beacon.hidden) {
      const r = el.getBoundingClientRect()
      this.beacon.style.top = `${r.top - 9}px`
      this.beacon.style.left = `${r.left + r.width - 9}px`
    }
    if (!tip || tip.hidden || tip.classList.contains('stept-centered')) return
    if (!el) {
      // Anchorless tip (a targetless tooltip): park it bottom-centre.
      tip.style.top = ''
      tip.style.left = ''
      tip.classList.add('stept-centered')
      return
    }
    const r = el.getBoundingClientRect()
    const tipRect = tip.getBoundingClientRect()
    const vp = { width: this.win.innerWidth, height: this.win.innerHeight }
    const pos = computeTooltipPosition(
      (step.placement as Placement) ?? 'auto',
      { top: r.top, left: r.left, width: r.width, height: r.height },
      { width: tipRect.width || 320, height: tipRect.height || 150 },
      vp,
      12,
      // The widget's own chrome (launcher, pill, open panel) must not cover
      // the tooltip it is pointing with.
      this.getObstructions?.() ?? [],
    )
    tip.style.top = `${pos.top}px`
    tip.style.left = `${pos.left}px`
  }

  /**
   * Fit the four pointer-catching panels around the spotlight cutout. Their
   * union is exactly the viewport minus the cutout, so the taught element is
   * the one place the backdrop lets a click through.
   */
  private positionBlockers(cut: Rect): void {
    const vw = this.win.innerWidth
    const vh = this.win.innerHeight
    const x0 = Math.max(0, Math.min(vw, cut.left))
    const x1 = Math.max(x0, Math.min(vw, cut.left + cut.width))
    const y0 = Math.max(0, Math.min(vh, cut.top))
    const y1 = Math.max(y0, Math.min(vh, cut.top + cut.height))
    const place = (index: number, top: number, left: number, width: number, height: number): void => {
      const blocker = this.blockers[index]
      if (!blocker) return
      Object.assign(blocker.style, {
        top: `${top}px`,
        left: `${left}px`,
        width: `${Math.max(0, width)}px`,
        height: `${Math.max(0, height)}px`,
      })
    }
    place(0, 0, 0, vw, y0) // above
    place(1, y1, 0, vw, vh - y1) // below
    place(2, y0, 0, x0, y1 - y0) // left of
    place(3, y0, x1, vw - x1, y1 - y0) // right of
  }

  // --- plumbing ------------------------------------------------------------

  private emit(event: TourEventName, stepIndex: number | null, extra?: TourEventMeta): void {
    const meta: TourEventMeta = {
      url: this.win.location?.href ?? '',
      viewport_w: this.win.innerWidth,
      ...(extra ?? {}),
    }
    this.onEvent(event, stepIndex, meta)
  }

  private persist(index: number, extra: { navigating?: boolean } = {}): void {
    if (this.preview || !this.tour) return
    writeTourProgress(this.storage, this.progressKey, {
      tourId: this.tour.id,
      stepIndex: index,
      startedAt: this.startedAt,
      updatedAt: Date.now(),
      // Only ever written truthy: a plain persist CONSUMES the flag, so a
      // resumed tour goes back to offering (not hijacking) on the next load.
      ...(extra.navigating ? { navigating: true } : {}),
    })
  }

  private clearProgress(): void {
    clearTourProgress(this.storage, this.progressKey)
  }

  private ensureStyle(): void {
    if (this.doc.getElementById(STYLE_ID)) return
    const style = this.doc.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    this.doc.head.appendChild(style)
  }

  private build(): void {
    const root = el2(this.doc, 'div', 'stept-tour-root')
    root.style.setProperty('--stept-accent', this.accent)
    const hole = el2(this.doc, 'div', 'stept-tour-hole')
    hole.hidden = true
    root.appendChild(hole)

    const tip = el2(this.doc, 'div', 'stept-tour-tip')
    tip.setAttribute('role', 'dialog')
    tip.setAttribute('tabindex', '-1')
    tip.hidden = true
    tip.style.setProperty('--stept-accent', this.accent)

    const banner = el2(this.doc, 'div', 'stept-tour-banner')
    banner.setAttribute('role', 'region')
    banner.hidden = true
    banner.style.setProperty('--stept-accent', this.accent)

    const beacon = el2(this.doc, 'button', 'stept-tour-beacon')
    beacon.hidden = true
    beacon.style.setProperty('--stept-accent', this.accent)

    const blockers: HTMLElement[] = []
    for (let i = 0; i < 4; i++) {
      const blocker = el2(this.doc, 'div', 'stept-tour-blocker')
      blocker.hidden = true
      blocker.setAttribute('aria-hidden', 'true')
      blockers.push(blocker)
    }

    // Siblings on <body> (not children of the pointer-events:none root) so they
    // stay clickable.
    this.doc.body.appendChild(root)
    this.doc.body.appendChild(tip)
    this.doc.body.appendChild(banner)
    this.doc.body.appendChild(beacon)
    for (const blocker of blockers) this.doc.body.appendChild(blocker)
    this.root = root
    this.hole = hole
    this.tip = tip
    this.banner = banner
    this.beacon = beacon
    this.blockers = blockers
  }

  private clearActing(): void {
    this.acting?.classList.remove('stept-tour-acting')
    this.acting = null
  }

  private resetStep(): void {
    for (const cleanup of this.stepCleanups.splice(0)) {
      try {
        cleanup()
      } catch {
        /* ignore */
      }
    }
    this.clearActing()
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    this.target = null
  }

  private teardown(): void {
    this.runToken++
    this.resetStep()
    this.win.removeEventListener('resize', this.reflow)
    this.win.removeEventListener('scroll', this.reflow, true)
    this.doc.removeEventListener('keydown', this.onKeyDown, true)
    this.root?.remove()
    this.tip?.remove()
    this.banner?.remove()
    this.beacon?.remove()
    for (const blocker of this.blockers) blocker.remove()
    this.blockers = []
    this.root = this.hole = this.tip = this.banner = this.beacon = null
    this.tour = null
    this.index = 0
    const previous = this.previousFocus as HTMLElement | null
    this.previousFocus = null
    if (previous && typeof previous.focus === 'function' && previous.isConnected) {
      try {
        previous.focus({ preventScroll: true })
      } catch {
        previous.focus()
      }
    }
  }
}

function el2(doc: Document, tag: string, className = ''): HTMLElement {
  const node = doc.createElement(tag)
  if (className) node.className = className
  return node
}

function defaultTitle(step: TourStep, i: number): string {
  if ((step.type ?? 'tooltip') === 'action' && step.action) return actionInstruction(step)
  return `Step ${i + 1}`
}

/** Guided-mode copy for an action step the USER has to perform. */
function actionInstruction(step: TourStep): string {
  const action = step.action
  if (!action) return ''
  if (action.kind === 'fill') return `Type “${action.value ?? ''}” here`
  if (action.kind === 'navigate') return `Go to ${action.url ?? 'the next page'}`
  return t('tour.click_target')
}

function actionHint(step: TourStep): string {
  return step.action?.kind === 'fill'
    ? t('tour.fill_field')
    : t('tour.click_target_continue')
}

function safeLocalStorage(win: Window): Storage | null {
  try {
    return win.localStorage ?? null
  } catch {
    return null
  }
}
