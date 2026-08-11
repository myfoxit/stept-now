/**
 * Date, number and size formatting.
 *
 * Everything here follows the *app's* language rather than the browser's. Those
 * disagree the moment someone picks a language in settings, and a German page
 * showing "1,234.5" and "Yesterday" is the tell that an app was translated
 * rather than localised.
 */

import { isToday, isYesterday, parseISO } from 'date-fns'

import { getLocale, t } from '@/i18n'

function toDate(value: string | Date): Date {
  return typeof value === 'string' ? parseISO(value) : value
}

/**
 * "2m", "3h", "5d" — compact relative time for lists.
 *
 * Built from catalog keys rather than from `date-fns`' English output. The
 * previous implementation string-replaced " minutes" → "m", which silently
 * produced English units inside every other language; the abbreviations
 * themselves are language-specific too ("d" is "日" in Japanese), so they have
 * to come from the catalog rather than from a `.replace` chain.
 */
export function timeAgo(value: string | Date, now: number = Date.now()): string {
  const then = toDate(value).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 60) return t('time.just_now')
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return t('time.minutes', { count: minutes })
  const hours = Math.round(minutes / 60)
  if (hours < 24) return t('time.hours', { count: hours })
  const days = Math.round(hours / 24)
  if (days < 30) return t('time.days', { count: days })
  const months = Math.round(days / 30)
  if (months < 12) return t('time.months', { count: months })
  return t('time.years', { count: Math.round(months / 12) })
}

/** "14:03" today, "Yesterday 14:03", else "12 Jan 14:03" — in the app's language. */
export function messageTime(value: string | Date): string {
  const date = toDate(value)
  const locale = getLocale()
  const clock = new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
  if (isToday(date)) return clock
  if (isYesterday(date)) return t('time.yesterday_at', { time: clock })
  const day = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(date)
  return `${day} ${clock}`
}

export function fullDateTime(value: string | Date): string {
  return new Intl.DateTimeFormat(getLocale(), {
    dateStyle: 'long',
    timeStyle: 'medium',
  }).format(toDate(value))
}

/** Locale-aware digit grouping — "1.234,5" in German, "1,234.5" in English. */
export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(getLocale(), options).format(value)
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join('')
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${formatNumber(bytes)} B`
  if (bytes < 1024 * 1024) {
    return `${formatNumber(bytes / 1024, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KB`
  }
  return `${formatNumber(bytes / (1024 * 1024), { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`
}
