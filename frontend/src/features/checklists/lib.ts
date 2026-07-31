/**
 * Pure helpers for the checklist editor — dependency-free so they unit-test
 * without React. The draft <-> wire mapping lives here because the item
 * `action` / `completion` tagged unions must serialize EXACTLY as the backend
 * validators expect (only the fields that belong to the selected type).
 */

import type {
  Checklist,
  ChecklistActionType,
  ChecklistCompletionType,
  ChecklistItem,
  ChecklistItemIn,
  FilterOp,
  SegmentFilter,
} from './api'

export const MAX_ITEMS = 20

export const ACTION_TYPES: { value: ChecklistActionType; label: string }[] = [
  { value: 'none', label: 'Nothing' },
  { value: 'start_tour', label: 'Start a tour' },
  { value: 'open_url', label: 'Open a URL' },
  { value: 'open_messenger', label: 'Open the messenger' },
]

export const COMPLETION_TYPES: { value: ChecklistCompletionType; label: string }[] = [
  { value: 'manual', label: 'User ticks it off' },
  { value: 'tour_completed', label: 'A tour is completed' },
  { value: 'url_visited', label: 'A URL is visited' },
]

export const POSITIONS = [
  { value: 'bottom-right', label: 'Bottom right' },
  { value: 'bottom-left', label: 'Bottom left' },
] as const

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
/** Local-only React key (server ids are uuid7 strings assigned on save). */
export function localId(prefix = 'row'): string {
  counter += 1
  return `${prefix}-${counter}-${Math.random().toString(36).slice(2, 7)}`
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

// --- items -------------------------------------------------------------------

export interface ChecklistItemDraft {
  key: string
  /** null for items that have never been saved. */
  id: string | null
  title: string
  body: string
  actionType: ChecklistActionType
  actionTourId: string
  actionUrl: string
  completionType: ChecklistCompletionType
  completionTourId: string
  completionUrlPattern: string
}

export function emptyItem(): ChecklistItemDraft {
  return {
    key: localId('item'),
    id: null,
    title: '',
    body: '',
    actionType: 'none',
    actionTourId: '',
    actionUrl: '',
    completionType: 'manual',
    completionTourId: '',
    completionUrlPattern: '',
  }
}

export function toItemDraft(item: ChecklistItem): ChecklistItemDraft {
  return {
    key: localId('item'),
    id: item.id,
    title: item.title,
    body: item.body ?? '',
    actionType: item.action?.type ?? 'none',
    actionTourId: item.action?.tour_id ?? '',
    actionUrl: item.action?.url ?? '',
    completionType: item.completion?.type ?? 'manual',
    completionTourId: item.completion?.tour_id ?? '',
    completionUrlPattern: item.completion?.url_pattern ?? '',
  }
}

/** Move the item at `from` to index `to`, returning a new array (immutable). */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return items
  }
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return next
}

/**
 * Draft → `ChecklistItemIn`. Only the fields the selected union member allows
 * are emitted, so the Pydantic validators never see a stray `url` on a
 * `start_tour` action.
 */
export function serializeItem(draft: ChecklistItemDraft): ChecklistItemIn {
  const action: ChecklistItemIn['action'] =
    draft.actionType === 'start_tour'
      ? { type: 'start_tour', tour_id: draft.actionTourId }
      : draft.actionType === 'open_url'
        ? { type: 'open_url', url: draft.actionUrl.trim() }
        : { type: draft.actionType }

  const completion: ChecklistItemIn['completion'] =
    draft.completionType === 'tour_completed'
      ? { type: 'tour_completed', tour_id: draft.completionTourId }
      : draft.completionType === 'url_visited'
        ? { type: 'url_visited', url_pattern: draft.completionUrlPattern.trim() }
        : { type: 'manual' }

  return {
    ...(draft.id ? { id: draft.id } : {}),
    title: draft.title.trim(),
    body: draft.body,
    action,
    completion,
  }
}

/** First client-side problem with the items, or null when they are savable. */
export function validateItems(drafts: ChecklistItemDraft[]): string | null {
  if (drafts.length > MAX_ITEMS) return `A checklist can hold at most ${MAX_ITEMS} items`
  for (const [index, draft] of drafts.entries()) {
    const where = `Item ${index + 1}`
    if (!draft.title.trim()) return `${where} needs a title`
    if (draft.actionType === 'start_tour' && !draft.actionTourId) {
      return `${where}: pick the tour its button starts`
    }
    if (draft.actionType === 'open_url' && !draft.actionUrl.trim()) {
      return `${where}: its button needs a URL`
    }
    if (draft.completionType === 'tour_completed' && !draft.completionTourId) {
      return `${where}: pick the tour that completes it`
    }
    if (draft.completionType === 'url_visited' && !draft.completionUrlPattern.trim()) {
      return `${where}: add the URL pattern that completes it`
    }
  }
  return null
}

// --- display -----------------------------------------------------------------

/** "On /app/*" or "Manual" — one-line trigger summary for list rows. */
export function describeTrigger(checklist: Pick<Checklist, 'trigger'>): string {
  if (checklist.trigger?.type !== 'url_match') return 'Manual'
  const pattern = checklist.trigger.url_pattern?.trim()
  return pattern ? `On ${pattern}` : 'On every page'
}

/** A 0..1 rate as a whole-number percentage: 0.732 → "73%". */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—'
  return `${Math.round(rate * 100)}%`
}
