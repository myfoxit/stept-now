/**
 * Pure helpers for the survey editor and results dashboard — dependency-free so
 * they unit-test without React. The draft <-> wire mapping lives here because
 * `options` is only legal on `select` questions and the backend rejects it
 * anywhere else.
 */

import type {
  FilterOp,
  QuestionType,
  SegmentFilter,
  Survey,
  SurveyNpsResult,
  SurveyQuestion,
  SurveyQuestionIn,
} from './api'

export const MAX_QUESTIONS = 10
export const MIN_OPTIONS = 2
export const MAX_OPTIONS = 6

export const QUESTION_TYPES: { value: QuestionType; label: string; hint: string }[] = [
  { value: 'nps', label: 'NPS (0–10)', hint: 'How likely are you to recommend us?' },
  { value: 'rating', label: 'Rating (1–5 stars)', hint: 'Rated one to five.' },
  { value: 'text', label: 'Open text', hint: 'A free-form answer.' },
  { value: 'select', label: 'Multiple choice', hint: 'Pick one of your options.' },
]

export const PRESENTATIONS = [
  { value: 'slideout', label: 'Slide-out' },
  { value: 'modal', label: 'Modal' },
] as const

export const FREQUENCIES: { value: string; label: string }[] = [
  { value: 'once', label: 'Once per contact' },
  { value: 'until_completed', label: 'Until completed' },
  { value: 'until_dismissed', label: 'Until dismissed' },
  { value: 'every_time', label: 'Every time' },
]

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

// --- questions ---------------------------------------------------------------

export interface QuestionOptionDraft {
  key: string
  value: string
}

export interface SurveyQuestionDraft {
  key: string
  /** null for questions that have never been saved. */
  id: string | null
  type: QuestionType
  question: string
  required: boolean
  options: QuestionOptionDraft[]
}

export function emptyOption(value = ''): QuestionOptionDraft {
  return { key: localId('option'), value }
}

export function emptyQuestion(): SurveyQuestionDraft {
  return {
    key: localId('question'),
    id: null,
    type: 'text',
    question: '',
    required: true,
    options: [emptyOption(), emptyOption()],
  }
}

export function toQuestionDraft(question: SurveyQuestion): SurveyQuestionDraft {
  const options = question.options ?? []
  return {
    key: localId('question'),
    id: question.id,
    type: question.type,
    question: question.question,
    required: question.required,
    options: options.length > 0 ? options.map((o) => emptyOption(o)) : [emptyOption(), emptyOption()],
  }
}

/** Move the item at `from` to index `to`, returning a new array (immutable). */
export function moveQuestion<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return items
  }
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return next
}

/** Draft → `SurveyQuestionIn`. `options` is emitted for select questions only. */
export function serializeQuestion(draft: SurveyQuestionDraft): SurveyQuestionIn {
  return {
    ...(draft.id ? { id: draft.id } : {}),
    type: draft.type,
    question: draft.question.trim(),
    required: draft.required,
    ...(draft.type === 'select'
      ? { options: draft.options.map((o) => o.value.trim()).filter(Boolean) }
      : {}),
  }
}

/** First client-side problem with the questions, or null when they are savable. */
export function validateQuestions(drafts: SurveyQuestionDraft[]): string | null {
  if (drafts.length > MAX_QUESTIONS) {
    return `A survey can hold at most ${MAX_QUESTIONS} questions`
  }
  for (const [index, draft] of drafts.entries()) {
    const where = `Question ${index + 1}`
    if (!draft.question.trim()) return `${where} needs some text`
    if (draft.type !== 'select') continue
    const options = draft.options.map((o) => o.value.trim()).filter(Boolean)
    if (options.length < MIN_OPTIONS || options.length > MAX_OPTIONS) {
      return `${where} needs between ${MIN_OPTIONS} and ${MAX_OPTIONS} options`
    }
    if (new Set(options).size !== options.length) return `${where} has duplicate options`
  }
  return null
}

// --- schedule ----------------------------------------------------------------

/** ISO instant → the `datetime-local` input value in the viewer's timezone. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`
}

/** `datetime-local` value → UTC ISO instant (null when blank/invalid). */
export function localInputToIso(value: string): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString()
}

// --- display -----------------------------------------------------------------

/** "On /app/*" or "Manual" — one-line trigger summary for list rows. */
export function describeTrigger(survey: Pick<Survey, 'trigger'>): string {
  if (survey.trigger?.type !== 'url_match') return 'Manual'
  const pattern = survey.trigger.url_pattern?.trim()
  return pattern ? `On ${pattern}` : 'On every page'
}

/** A 0..1 rate as a whole-number percentage: 0.732 → "73%". */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—'
  return `${Math.round(rate * 100)}%`
}

/** Short axis label for a YYYY-MM-DD date: "12 Jan". */
export function shortDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export interface NpsSegment {
  key: 'promoters' | 'passives' | 'detractors'
  label: string
  count: number
  /** 0..100, rounded to one decimal, for the stacked meter widths. */
  percent: number
}

/**
 * Promoter/passive/detractor shares of the classified responses. The score
 * itself is computed server-side (`%promoters − %detractors`); this only
 * derives the widths and labels the card renders.
 */
export function npsSegments(nps: SurveyNpsResult): { total: number; segments: NpsSegment[] } {
  const total = nps.promoters + nps.passives + nps.detractors
  const pct = (count: number) => (total === 0 ? 0 : Math.round((count / total) * 1000) / 10)
  return {
    total,
    segments: [
      { key: 'promoters', label: 'Promoters', count: nps.promoters, percent: pct(nps.promoters) },
      { key: 'passives', label: 'Passives', count: nps.passives, percent: pct(nps.passives) },
      {
        key: 'detractors',
        label: 'Detractors',
        count: nps.detractors,
        percent: pct(nps.detractors),
      },
    ],
  }
}

/** Rating distribution as ordered 1..5 rows (missing buckets become 0). */
export function ratingRows(distribution: Record<string, number>): { rating: string; count: number }[] {
  return ['1', '2', '3', '4', '5'].map((rating) => ({
    rating,
    count: distribution[rating] ?? 0,
  }))
}

/** Select counts as rows sorted by count desc, then option label. */
export function selectRows(counts: Record<string, number>): { option: string; count: number }[] {
  return Object.entries(counts)
    .map(([option, count]) => ({ option, count }))
    .sort((a, b) => b.count - a.count || a.option.localeCompare(b.option))
}
