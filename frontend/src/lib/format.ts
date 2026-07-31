import { format, formatDistanceToNowStrict, isToday, isYesterday, parseISO } from 'date-fns'

function toDate(value: string | Date): Date {
  return typeof value === 'string' ? parseISO(value) : value
}

/** "2m", "3h", "5d" — compact relative time for lists. */
export function timeAgo(value: string | Date): string {
  return formatDistanceToNowStrict(toDate(value), { addSuffix: false })
    .replace(' seconds', 's')
    .replace(' second', 's')
    .replace(' minutes', 'm')
    .replace(' minute', 'm')
    .replace(' hours', 'h')
    .replace(' hour', 'h')
    .replace(' days', 'd')
    .replace(' day', 'd')
    .replace(' months', 'mo')
    .replace(' month', 'mo')
    .replace(' years', 'y')
    .replace(' year', 'y')
}

/** "14:03" today, "Yesterday 14:03", else "12 Jan 14:03". */
export function messageTime(value: string | Date): string {
  const date = toDate(value)
  if (isToday(date)) return format(date, 'HH:mm')
  if (isYesterday(date)) return `Yesterday ${format(date, 'HH:mm')}`
  return format(date, 'd MMM HH:mm')
}

export function fullDateTime(value: string | Date): string {
  return format(toDate(value), 'PPpp')
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
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
