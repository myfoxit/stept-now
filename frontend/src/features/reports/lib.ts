/** Pure formatting helpers for report KPIs — unit-tested. */

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

/** A 0..1 rate as a whole-number percentage: 0.732 → "73%". */
export function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—'
  return `${Math.round(rate * 100)}%`
}

/** Minutes as a human duration: 42 → "42m", 130 → "2.2h", null → "—". */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—'
  if (minutes < 60) return `${Math.round(minutes)}m`
  const hours = minutes / 60
  if (hours < 24) return `${hours % 1 === 0 ? hours : hours.toFixed(1)}h`
  const days = hours / 24
  return `${days % 1 === 0 ? days : days.toFixed(1)}d`
}

export const DAY_RANGES = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
] as const

const CHANNEL_LABELS: Record<string, string> = {
  widget: 'Widget',
  email: 'Email',
  slack: 'Slack',
  telegram: 'Telegram',
  api: 'API',
}

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel
}

/** Short axis label for a YYYY-MM-DD date: "12 Jan". */
export function shortDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return date
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
