/**
 * Pure helpers for the knowledge area (labels + KPI formatting).
 * Formatting mirrors `features/reports/lib.ts` so analytics screens read identically;
 * kept local because feature folders are owned independently.
 */

/** Compact large counts: 1,284 → "1.3K", 4.2M. */
export function compactNumber(value: number): string {
  if (Math.abs(value) < 1000) return String(value)
  if (Math.abs(value) < 1_000_000) {
    const k = value / 1000
    return `${k % 1 === 0 ? k : k.toFixed(1)}K`
  }
  const m = value / 1_000_000
  return `${m % 1 === 0 ? m : m.toFixed(1)}M`
}

/** A 0..1 rate as a whole-number percentage: 0.732 → "73%". */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—'
  return `${Math.round(rate * 100)}%`
}

/** A relevance score in 0..1: 0.4213 → "0.42", null → "—". */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return score.toFixed(2)
}

/** Short axis label for a YYYY-MM-DD date: "12 Jan". */
export function shortDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export const DAY_RANGES = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
] as const

const SOURCE_TYPE_LABELS: Record<string, string> = {
  files: 'Files',
  urls: 'URLs',
  text: 'Text',
  sitemap: 'Sitemap',
  crawl: 'Web crawl',
  github: 'GitHub',
  notion: 'Notion',
  articles: 'Articles',
}

export function sourceTypeLabel(type: string): string {
  return SOURCE_TYPE_LABELS[type] ?? type
}

/** Mime types the authored-document editor (PATCH /knowledge/documents/{id}) accepts. */
const EDITABLE_MIMES = ['text/markdown', 'text/plain']

/**
 * Whether a document is worth offering an "Edit" action for — the same rule the
 * backend enforces: authored (storage-backed) text living in a files/text
 * source. Connector- and URL-backed documents answer 409 to a PATCH, so they
 * get no affordance at all. The detail endpoint is still the authority: it
 * returns `content: null` for anything it will not let you edit.
 */
export function isEditableDocument(
  document: { mime?: string | null; uri?: string | null },
  sourceType: string | undefined
): boolean {
  if (sourceType !== 'files' && sourceType !== 'text') return false
  if (document.uri) return false
  return EDITABLE_MIMES.includes(document.mime ?? '')
}

/** The configured auto-resync interval, if the source has one (int minutes ≥ 5). */
export function refreshMinutes(config: Record<string, unknown> | null | undefined): number | null {
  const raw = config?.refresh_minutes
  const value = typeof raw === 'string' ? Number(raw) : raw
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 5) return null
  return Math.round(value)
}
