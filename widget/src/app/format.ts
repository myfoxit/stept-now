import { getLocale, t } from '../i18n'

/** Compact "time ago" for conversation rows and message timestamps. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const secs = Math.max(0, Math.round((now - then) / 1000))
  if (secs < 60) return t('time.just_now')
  const mins = Math.round(secs / 60)
  if (mins < 60) return t('time.minutes', { count: mins })
  const hours = Math.round(mins / 60)
  if (hours < 24) return t('time.hours', { count: hours })
  const days = Math.round(hours / 24)
  if (days < 7) return t('time.days', { count: days })
  // Past a week we show a real date, which must follow the widget's language
  // rather than the browser's — those disagree whenever we picked the locale
  // from what the visitor writes.
  return new Date(then).toLocaleDateString(getLocale(), { month: 'short', day: 'numeric' })
}

/** Clock time for a single message (e.g. "3:42 PM"). */
export function clockTime(iso: string): string {
  const time = new Date(iso)
  if (Number.isNaN(time.getTime())) return ''
  return time.toLocaleTimeString(getLocale(), { hour: 'numeric', minute: '2-digit' })
}

/**
 * Trim a conversation preview at a word boundary (~`max` chars).
 *
 * "Einrich…" mid-word reads broken; "Einrichtung deiner…" reads intentional.
 * Only falls back to a hard cut when the text has no usable break (one giant
 * token — a URL, or CJK text without spaces, which breaks fine anywhere).
 */
export function trimPreview(text: string, max = 60): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (clean.length <= max) return clean
  const slice = clean.slice(0, max + 1)
  const lastBreak = slice.lastIndexOf(' ')
  const cut = lastBreak > max * 0.4 ? slice.slice(0, lastBreak) : clean.slice(0, max)
  return `${cut.replace(/[\s.,;:!،、。]+$/, '')}…`
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}
