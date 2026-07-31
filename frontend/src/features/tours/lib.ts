/**
 * Pure helpers for the tour editor — dependency-free so they unit-test without
 * React. The draft <-> wire mapping lives here because the v2 step schema is a
 * set of tagged unions (`action` only on action steps, `wait` only on wait
 * steps) and because two fields — `target` and `screenshot_key` — are recorder
 * artifacts the dashboard must carry through untouched.
 */

import type {
  FilterOp,
  SegmentFilter,
  StepPlacement,
  StepType,
  Tour,
  TourCreate,
  TourDayStat,
  TourEvent,
  TourEventsPage,
  TourKind,
  TourStep,
  TourStepIn,
  TourUpdate,
} from './api'

export const MAX_FALLBACK_SELECTORS = 5

// --- option catalogs ---------------------------------------------------------

export const TOUR_KINDS: { value: TourKind; label: string; hint: string }[] = [
  { value: 'flow', label: 'Flow', hint: 'A multi-step walkthrough anchored to your UI' },
  { value: 'banner', label: 'Banner', hint: 'A single bar docked to the top or bottom' },
  { value: 'announcement', label: 'Announcement', hint: 'A single centered modal' },
]

export const STEP_TYPES: { value: StepType; label: string; hint: string }[] = [
  { value: 'tooltip', label: 'Tooltip', hint: 'Bubble anchored to an element' },
  { value: 'modal', label: 'Modal', hint: 'Centered card, no anchor' },
  { value: 'banner', label: 'Banner', hint: 'Full-width bar' },
  { value: 'hotspot', label: 'Hotspot', hint: 'Pulsing beacon that opens the tooltip' },
  { value: 'action', label: 'Action', hint: 'Driven mode performs it for the user' },
  { value: 'wait', label: 'Wait', hint: 'Hidden step: pause until something happens' },
]

export const PLACEMENTS: { value: StepPlacement; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'top', label: 'Top' },
  { value: 'bottom', label: 'Bottom' },
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
  { value: 'center', label: 'Center' },
]

export type AdvanceOn = 'button' | 'element_click' | 'input' | 'delay'
export const ADVANCE_MODES: { value: AdvanceOn; label: string }[] = [
  { value: 'button', label: 'Next button' },
  { value: 'element_click', label: 'User clicks the element' },
  { value: 'input', label: 'User types into the element' },
  { value: 'delay', label: 'After a delay' },
]

export type ActionKind = 'click' | 'fill' | 'navigate'
export const ACTION_KINDS: { value: ActionKind; label: string }[] = [
  { value: 'click', label: 'Click the element' },
  { value: 'fill', label: 'Fill the element' },
  { value: 'navigate', label: 'Go to a URL' },
]

export type WaitFor = 'element' | 'url'
export const WAIT_FOR: { value: WaitFor; label: string }[] = [
  { value: 'element', label: 'An element appears' },
  { value: 'url', label: 'The URL matches' },
]

export type FrequencyType = 'once' | 'until_completed' | 'until_dismissed' | 'every_time'
export const FREQUENCY_TYPES: { value: FrequencyType; label: string }[] = [
  { value: 'once', label: 'Once per contact' },
  { value: 'until_completed', label: 'Until they complete it' },
  { value: 'until_dismissed', label: 'Until completed or dismissed' },
  { value: 'every_time', label: 'Every matching visit' },
]

export type TourMode = 'guided' | 'driven'
export const MODES: { value: TourMode; label: string; hint: string }[] = [
  { value: 'guided', label: 'Guided', hint: 'The user performs each step' },
  { value: 'driven', label: 'Do it for me', hint: 'Action steps run automatically' },
]

/** Types that anchor to a page element and therefore need a selector. */
const SELECTOR_TYPES: StepType[] = ['tooltip', 'hotspot', 'action']
/** Types that render authored content (title / body / media). */
const CONTENT_TYPES: StepType[] = ['tooltip', 'modal', 'banner', 'hotspot']

export function needsSelector(type: StepType): boolean {
  return SELECTOR_TYPES.includes(type)
}

export function hasContent(type: StepType): boolean {
  return CONTENT_TYPES.includes(type)
}

// --- audience filters (shared DSL with segments) -----------------------------

export const FILTER_FIELDS = [
  { value: 'email', label: 'Email' },
  { value: 'name', label: 'Name' },
  { value: 'external_id', label: 'External ID' },
  { value: 'last_seen_at', label: 'Last seen' },
  { value: 'created_at', label: 'Created' },
  { value: 'verified', label: 'Verified' },
  { value: 'attributes', label: 'Custom attribute' },
] as const

export const FILTER_OPS: { value: FilterOp; label: string }[] = [
  { value: 'eq', label: 'is' },
  { value: 'neq', label: 'is not' },
  { value: 'contains', label: 'contains' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'gt', label: 'is after / greater than' },
  { value: 'lt', label: 'is before / less than' },
  { value: 'exists', label: 'exists' },
  { value: 'not_exists', label: 'does not exist' },
]

/** `exists` / `not_exists` are unary — the value input is hidden for them. */
export function opNeedsValue(op: FilterOp): boolean {
  return op !== 'exists' && op !== 'not_exists'
}

export interface FilterDraft {
  key: string
  /** One of FILTER_FIELDS values; `attributes` pairs with `attrKey`. */
  field: string
  attrKey: string
  op: FilterOp
  value: string
}

let counter = 0
/** Local-only React key (server step ids are uuid7 strings assigned on save). */
export function localId(prefix = 'row'): string {
  counter += 1
  return `${prefix}-${counter}-${Math.random().toString(36).slice(2, 7)}`
}

/** Back-compat alias used by the step list. */
export function localStepId(): string {
  return localId('step')
}

export function emptyFilter(): FilterDraft {
  return { key: localId('filter'), field: 'email', attrKey: '', op: 'eq', value: '' }
}

export function toFilterDraft(filter: SegmentFilter): FilterDraft {
  const isAttr = filter.field.startsWith('attributes.')
  return {
    key: localId('filter'),
    field: isAttr ? 'attributes' : filter.field,
    attrKey: isAttr ? filter.field.slice('attributes.'.length) : '',
    op: filter.op,
    value: filter.value === null || filter.value === undefined ? '' : String(filter.value),
  }
}

/** "true"/"false" become booleans so `verified` filters compare correctly. */
export function coerceFilterValue(raw: string): string | boolean {
  const trimmed = raw.trim()
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  return trimmed
}

/** Drop incomplete rows; unary ops send no value at all. */
export function serializeFilters(rows: FilterDraft[]): SegmentFilter[] {
  const out: SegmentFilter[] = []
  for (const row of rows) {
    const field = row.field === 'attributes' ? `attributes.${row.attrKey.trim()}` : row.field
    if (field === 'attributes.') continue
    if (!opNeedsValue(row.op)) {
      out.push({ field, op: row.op })
      continue
    }
    if (row.value.trim() === '') continue
    out.push({ field, op: row.op, value: coerceFilterValue(row.value) })
  }
  return out
}

// --- steps -------------------------------------------------------------------

export interface StepDraft {
  /** Stable React key; survives reordering. */
  key: string
  /** Server step id, or null for a step that has never been saved. */
  id: string | null
  type: StepType
  selector: string
  fallbackSelectors: string[]
  textHint: string
  /** Opaque @stept/dom-capture descriptor from the recorder — passthrough only. */
  target: Record<string, unknown> | null
  screenshotKey: string | null
  title: string
  body: string
  mediaType: 'image' | 'video'
  mediaUrl: string
  placement: StepPlacement
  advanceOn: AdvanceOn
  /** Kept as a string so the number input can be cleared while typing. */
  delayMs: string
  actionKind: ActionKind
  actionValue: string
  actionUrl: string
  waitFor: WaitFor
  waitSelector: string
  waitUrlPattern: string
  waitTimeoutMs: string
}

export function emptyStep(type: StepType = 'tooltip'): StepDraft {
  return {
    key: localId('step'),
    id: null,
    type,
    selector: '',
    fallbackSelectors: [],
    textHint: '',
    target: null,
    screenshotKey: null,
    title: '',
    body: '',
    mediaType: 'image',
    mediaUrl: '',
    placement: 'auto',
    advanceOn: 'button',
    delayMs: '3000',
    actionKind: 'click',
    actionValue: '',
    actionUrl: '',
    waitFor: 'element',
    waitSelector: '',
    waitUrlPattern: '',
    waitTimeoutMs: '10000',
  }
}

export function toStepDraft(step: TourStep): StepDraft {
  const base = emptyStep()
  return {
    ...base,
    key: localId('step'),
    id: step.id ?? null,
    type: (step.type ?? 'tooltip') as StepType,
    selector: step.selector ?? '',
    fallbackSelectors: (step.fallback_selectors ?? []).slice(0, MAX_FALLBACK_SELECTORS),
    textHint: step.text_hint ?? '',
    target: (step.target as Record<string, unknown> | null) ?? null,
    screenshotKey: step.screenshot_key ?? null,
    title: step.title ?? '',
    body: step.body ?? '',
    mediaType: step.media?.type ?? 'image',
    mediaUrl: step.media?.url ?? '',
    placement: (step.placement ?? 'auto') as StepPlacement,
    advanceOn: (step.advance?.on ?? 'button') as AdvanceOn,
    delayMs: step.advance?.delay_ms != null ? String(step.advance.delay_ms) : base.delayMs,
    actionKind: (step.action?.kind ?? 'click') as ActionKind,
    actionValue: step.action?.value ?? '',
    actionUrl: step.action?.url ?? '',
    waitFor: (step.wait?.for ?? 'element') as WaitFor,
    waitSelector: step.wait?.selector ?? '',
    waitUrlPattern: step.wait?.url_pattern ?? '',
    waitTimeoutMs:
      step.wait?.timeout_ms != null ? String(step.wait.timeout_ms) : base.waitTimeoutMs,
  }
}

function toInt(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

/**
 * Draft → `TourStepIn`. Only the fields the selected step type allows are
 * emitted, so the Pydantic validators never see a stray `wait` on a tooltip.
 * `target` / `screenshot_key` are echoed back verbatim — losing them would
 * downgrade a recorder-captured step to a bare CSS selector.
 */
export function serializeStep(draft: StepDraft): TourStepIn {
  const step: TourStepIn = {
    ...(draft.id ? { id: draft.id } : {}),
    type: draft.type,
    selector: draft.selector.trim(),
    fallback_selectors: draft.fallbackSelectors
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, MAX_FALLBACK_SELECTORS),
    text_hint: draft.textHint.trim().slice(0, 80),
    title: draft.title,
    body: draft.body,
    placement: draft.placement,
    advance:
      draft.advanceOn === 'delay'
        ? { on: 'delay', delay_ms: toInt(draft.delayMs, 3000) }
        : { on: draft.advanceOn },
  }
  if (draft.target) step.target = draft.target
  if (draft.screenshotKey) step.screenshot_key = draft.screenshotKey
  if (draft.mediaUrl.trim()) {
    step.media = { type: draft.mediaType, url: draft.mediaUrl.trim() }
  }
  if (draft.type === 'action') {
    step.action = {
      kind: draft.actionKind,
      ...(draft.actionKind === 'fill' ? { value: draft.actionValue } : {}),
      ...(draft.actionKind === 'navigate' ? { url: draft.actionUrl.trim() } : {}),
    }
  }
  if (draft.type === 'wait') {
    step.wait = {
      for: draft.waitFor,
      timeout_ms: toInt(draft.waitTimeoutMs, 10000),
      ...(draft.waitFor === 'element'
        ? { selector: (draft.waitSelector || draft.selector).trim() }
        : { url_pattern: draft.waitUrlPattern.trim() }),
    }
  }
  return step
}

/** First client-side problem with the steps, or null when they are savable. */
export function validateSteps(drafts: StepDraft[]): string | null {
  for (const [index, draft] of drafts.entries()) {
    const where = `Step ${index + 1}`
    if (needsSelector(draft.type) && !draft.selector.trim()) {
      return `${where} needs a CSS selector`
    }
    if (draft.advanceOn === 'delay' && toInt(draft.delayMs, 0) < 100) {
      return `${where}: the delay must be at least 100ms`
    }
    if (draft.type === 'action') {
      if (draft.actionKind === 'fill' && !draft.actionValue) {
        return `${where}: a fill action needs a value to type`
      }
      if (draft.actionKind === 'navigate' && !draft.actionUrl.trim()) {
        return `${where}: a navigate action needs a URL`
      }
    }
    if (draft.type === 'wait') {
      if (draft.waitFor === 'url' && !draft.waitUrlPattern.trim()) {
        return `${where}: waiting for a URL needs a pattern`
      }
      if (draft.waitFor === 'element' && !draft.waitSelector.trim() && !draft.selector.trim()) {
        return `${where}: waiting for an element needs a selector`
      }
    }
  }
  return null
}

/** Move the item at `from` to index `to`, returning a new array (immutable). */
export function moveStep<T>(steps: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= steps.length || to >= steps.length) {
    return steps
  }
  const next = [...steps]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return next
}

/** Add a fallback selector, de-duplicated and capped. Returns the same array when rejected. */
export function addFallback(list: string[], value: string): string[] {
  const trimmed = value.trim()
  if (!trimmed || list.length >= MAX_FALLBACK_SELECTORS || list.includes(trimmed)) return list
  return [...list, trimmed]
}

// --- tour draft --------------------------------------------------------------

export interface TourDraft {
  name: string
  description: string
  kind: TourKind
  triggerType: 'manual' | 'url_match'
  urlPattern: string
  accent: string
  bannerPosition: 'top' | 'bottom'
  audienceType: 'all' | 'filters'
  filters: FilterDraft[]
  /** `datetime-local` input values (local time), empty = unset. */
  startAt: string
  endAt: string
  frequencyType: FrequencyType
  cooldownHours: string
  priority: string
  mode: TourMode
  backdrop: boolean
  showProgress: boolean
  dismissable: boolean
}

export function toTourDraft(tour: Tour): TourDraft {
  return {
    name: tour.name,
    description: tour.description ?? '',
    kind: (tour.kind ?? 'flow') as TourKind,
    triggerType: tour.trigger?.type ?? 'manual',
    urlPattern: tour.trigger?.url_pattern ?? '',
    accent: tour.theme?.accent ?? '#6366f1',
    bannerPosition: tour.theme?.position ?? 'bottom',
    audienceType: tour.audience?.type ?? 'all',
    filters: (tour.audience?.filters ?? []).map(toFilterDraft),
    startAt: isoToLocalInput(tour.schedule?.start_at),
    endAt: isoToLocalInput(tour.schedule?.end_at),
    frequencyType: (tour.frequency?.type ?? 'until_dismissed') as FrequencyType,
    cooldownHours:
      tour.frequency?.cooldown_hours != null ? String(tour.frequency.cooldown_hours) : '',
    priority: String(tour.priority ?? 0),
    mode: (tour.settings?.mode ?? 'guided') as TourMode,
    backdrop: tour.settings?.backdrop ?? true,
    showProgress: tour.settings?.show_progress ?? true,
    dismissable: tour.settings?.dismissable ?? true,
  }
}

/**
 * Draft + steps → `TourUpdate`. Everything the editor owns is sent on every
 * save, including `audience` — which the v1 editor silently dropped, so
 * targeting configured in the UI never reached the API.
 */
export function serializeTour(draft: TourDraft, steps: StepDraft[]): TourUpdate {
  return {
    name: draft.name.trim(),
    description: draft.description,
    kind: draft.kind,
    trigger: {
      type: draft.triggerType,
      url_pattern: draft.triggerType === 'url_match' ? draft.urlPattern.trim() : null,
    },
    audience: {
      type: draft.audienceType,
      filters: draft.audienceType === 'filters' ? serializeFilters(draft.filters) : [],
    },
    schedule: {
      start_at: localInputToIso(draft.startAt),
      end_at: localInputToIso(draft.endAt),
    },
    frequency: {
      type: draft.frequencyType,
      cooldown_hours:
        draft.frequencyType === 'every_time' && draft.cooldownHours.trim()
          ? toInt(draft.cooldownHours, 24)
          : null,
    },
    priority: toInt(draft.priority, 0),
    settings: {
      mode: draft.mode,
      backdrop: draft.backdrop,
      show_progress: draft.showProgress,
      dismissable: draft.dismissable,
    },
    theme: {
      accent: draft.accent,
      ...(draft.kind === 'banner' ? { position: draft.bannerPosition } : {}),
    },
    steps: steps.map(serializeStep),
  }
}

/** Payload for "Duplicate": everything but ids, status, stats and step ids. */
export function duplicatePayload(tour: Tour): TourCreate {
  return {
    name: `${tour.name} (copy)`,
    description: tour.description ?? '',
    kind: (tour.kind ?? 'flow') as TourKind,
    trigger: tour.trigger,
    audience: tour.audience,
    schedule: tour.schedule,
    frequency: tour.frequency,
    priority: tour.priority ?? 0,
    settings: tour.settings,
    theme: tour.theme,
    // Fresh ids: two tours must never share step ids (analytics key off index,
    // but the recorder and the player both treat step ids as tour-local).
    steps: (tour.steps ?? []).map((step) => serializeStep({ ...toStepDraft(step), id: null })),
  }
}

// --- dates -------------------------------------------------------------------

/** ISO instant → `datetime-local` input value in the viewer's timezone. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`
}

/** `datetime-local` value (local time) → ISO instant, or null when empty. */
export function localInputToIso(value: string): string | null {
  if (!value.trim()) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

// --- display -----------------------------------------------------------------

/** A 0..1 rate as a whole-number percentage: 0.732 → "73%". */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—'
  return `${Math.round(rate * 100)}%`
}

/** Compact large counts: 1,284 / 12.9K / 4.2M. */
export function compactNumber(value: number): string {
  if (Math.abs(value) < 1000) return String(value)
  if (Math.abs(value) < 1_000_000) {
    const k = value / 1000
    return `${k % 1 === 0 ? k : k.toFixed(1)}K`
  }
  const m = value / 1_000_000
  return `${m % 1 === 0 ? m : m.toFixed(1)}M`
}

/** Short axis label for a YYYY-MM-DD date: "12 Jan". */
export function shortDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function kindLabel(kind: string): string {
  return TOUR_KINDS.find((k) => k.value === kind)?.label ?? kind
}

export function stepTypeLabel(type: string): string {
  return STEP_TYPES.find((t) => t.value === type)?.label ?? type
}

/** One-line summary shown in the collapsed step header. */
export function describeStep(draft: StepDraft): string {
  if (draft.type === 'wait') {
    return draft.waitFor === 'url'
      ? `Wait for URL ${draft.waitUrlPattern || '…'}`
      : `Wait for ${draft.waitSelector || draft.selector || 'an element'}`
  }
  if (draft.type === 'action') {
    const kind = ACTION_KINDS.find((k) => k.value === draft.actionKind)?.label ?? draft.actionKind
    return `${kind} ${draft.selector}`.trim()
  }
  return draft.title || draft.selector || stepTypeLabel(draft.type)
}

/** Public media URL for a recorder screenshot key (served without auth). */
export function mediaSrc(workspaceId: string, key: string): string {
  return `/api/widget/media/${workspaceId}/${key}`
}

/** The link an author pastes into their browser to preview an unpublished tour. */
export function previewLink(token: string, origin = 'https://your-site.example.com'): string {
  return `${origin}/#stept-preview=${token}`
}

// --- analytics ---------------------------------------------------------------

const EVENT_LABELS: Record<string, string> = {
  started: 'Started',
  step_viewed: 'Step viewed',
  completed: 'Completed',
  dismissed: 'Dismissed',
  step_error: 'Step error',
}

export function eventLabel(event: string): string {
  return EVENT_LABELS[event] ?? event
}

/** Trim the trailing window to the days that actually carry data (plus padding). */
export function byDaySeries(byDay: TourDayStat[] | undefined): TourDayStat[] {
  const days = byDay ?? []
  const lastActive = days.reduce(
    (acc, day, index) => (day.starts > 0 || day.completions > 0 ? index : acc),
    -1
  )
  if (lastActive === -1) return days
  const firstActive = days.findIndex((day) => day.starts > 0 || day.completions > 0)
  const from = Math.max(0, firstActive - 1)
  return days.slice(from, Math.min(days.length, lastActive + 2))
}

/** True when the by-day window has nothing to plot. */
export function isEmptySeries(byDay: TourDayStat[] | undefined): boolean {
  return !(byDay ?? []).some((day) => day.starts > 0 || day.completions > 0)
}

/**
 * Prepend a realtime event to a cached page, keeping the page size stable.
 * Used for live-append on the newest-first event feed.
 */
export function prependEvent(page: TourEventsPage, event: TourEvent): TourEventsPage {
  if (page.items.some((item) => item.id === event.id)) return page
  return {
    ...page,
    items: [event, ...page.items].slice(0, page.limit),
    total: page.total + 1,
  }
}
