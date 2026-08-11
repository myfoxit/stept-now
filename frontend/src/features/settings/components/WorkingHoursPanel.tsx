/**
 * Per-inbox working hours.
 *
 * The whole week is replaced in one PUT — a partially-saved schedule is almost
 * always a bug, and full replacement keeps the (inbox, day) uniqueness trivially
 * true. Times are the inbox's local wall clock in the timezone chosen here.
 */

import { Clock } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'
import { useInboxes, useSaveWorkingHours, useWorkingHours } from '@/features/inbox/hooks'
import { t } from '@/i18n'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

interface DayState {
  closed: boolean
  open: string // HH:MM
  close: string
}

const DEFAULT_DAY: DayState = { closed: false, open: '09:00', close: '17:00' }

function toClock(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  return `${String(hours).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

function toMinutes(clock: string): number {
  const [hours, minutes] = clock.split(':').map(Number)
  return (hours || 0) * 60 + (minutes || 0)
}

/** A small, honest list — the API accepts any IANA name typed into the box. */
const COMMON_ZONES = [
  'UTC',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Madrid',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
]

export function WorkingHoursPanel() {
  const canManage = useHasPerm('channels:manage')
  const { data: inboxes = [], isLoading: inboxesLoading } = useInboxes()
  const [inboxId, setInboxId] = useState<string | null>(null)

  useEffect(() => {
    if (!inboxId && inboxes.length > 0) setInboxId(inboxes[0].id)
  }, [inboxes, inboxId])

  if (inboxesLoading) return <Skeleton className="h-64 w-full" />
  if (inboxes.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t('settings.create_an_inbox_first_working_hours')}
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid max-w-xs gap-1.5">
        <Label htmlFor="wh-inbox">{t('common.inbox')}</Label>
        <Select value={inboxId ?? ''} onValueChange={setInboxId}>
          <SelectTrigger id="wh-inbox">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {inboxes.map((inbox) => (
              <SelectItem key={inbox.id} value={inbox.id}>
                {inbox.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {inboxId ? <InboxHours key={inboxId} inboxId={inboxId} canManage={canManage} /> : null}
    </div>
  )
}

function InboxHours({ inboxId, canManage }: { inboxId: string; canManage: boolean }) {
  const { data, isLoading } = useWorkingHours(inboxId)
  const save = useSaveWorkingHours(inboxId)

  const [enabled, setEnabled] = useState(false)
  const [timezone, setTimezone] = useState('UTC')
  const [message, setMessage] = useState('')
  const [days, setDays] = useState<DayState[]>(() =>
    DAYS.map((_, index) => ({ ...DEFAULT_DAY, closed: index >= 5 }))
  )

  useEffect(() => {
    if (!data) return
    setEnabled(data.enabled)
    setTimezone(data.timezone)
    setMessage(data.out_of_office_message ?? '')
    const rows = data.days ?? []
    if (rows.length > 0) {
      const next = DAYS.map((_, index) => {
        const row = rows.find((d) => d.day_of_week === index)
        if (!row) return { ...DEFAULT_DAY, closed: true }
        return {
          closed: row.closed_all_day,
          open: row.open_all_day ? '00:00' : toClock(row.open_minute),
          close: row.open_all_day ? '24:00' : toClock(row.close_minute),
        }
      })
      setDays(next)
    }
  }, [data])

  function update(index: number, patch: Partial<DayState>) {
    setDays((prev) => prev.map((day, i) => (i === index ? { ...day, ...patch } : day)))
  }

  const invalid = days.some((day) => !day.closed && toMinutes(day.close) <= toMinutes(day.open))

  function submit() {
    save.mutate({
      enabled,
      timezone,
      out_of_office_message: message.trim() || null,
      days: days.map((day, index) => ({
        day_of_week: index,
        closed_all_day: day.closed,
        open_minute: toMinutes(day.open),
        close_minute: toMinutes(day.close),
      })),
    })
  }

  if (isLoading) return <Skeleton className="h-64 w-full" />

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Clock className="size-4 text-muted-foreground" />
        <CardTitle className="text-base">{t('settings.working_hours')}</CardTitle>
        {data ? (
          <span
            className={
              data.currently_open
                ? 'ml-auto rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300'
                : 'ml-auto rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground'
            }
          >
            {data.currently_open ? 'Open now' : 'Closed now'}
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={enabled}
            disabled={!canManage}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>
            <span className="font-medium">{t('settings.enforce_working_hours')}</span>
            <span className="block text-xs text-muted-foreground">
              {t('settings.drives_out_of_office_replies_and')}
            </span>
          </span>
        </label>

        <div className="grid max-w-xs gap-1.5">
          <Label htmlFor="wh-tz">{t('settings.timezone')}</Label>
          <Input
            id="wh-tz"
            list="wh-zones"
            value={timezone}
            disabled={!canManage}
            onChange={(e) => setTimezone(e.target.value)}
          />
          <datalist id="wh-zones">
            {COMMON_ZONES.map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </div>

        <div className="space-y-2">
          {DAYS.map((label, index) => {
            const day = days[index]
            return (
              <div key={label} className="flex flex-wrap items-center gap-2">
                <span className="w-24 text-sm">{label}</span>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    className="size-3.5 accent-primary"
                    checked={!day.closed}
                    disabled={!canManage}
                    onChange={(e) => update(index, { closed: !e.target.checked })}
                    aria-label={`${label} open`}
                  />
                  {t('common.open')}
                </label>
                <Input
                  type="time"
                  className="h-8 w-28"
                  value={day.open}
                  disabled={!canManage || day.closed}
                  onChange={(e) => update(index, { open: e.target.value })}
                  aria-label={`${label} opening time`}
                />
                <span className="text-xs text-muted-foreground">{t('settings.to')}</span>
                <Input
                  type="time"
                  className="h-8 w-28"
                  value={day.close}
                  disabled={!canManage || day.closed}
                  onChange={(e) => update(index, { close: e.target.value })}
                  aria-label={`${label} closing time`}
                />
              </div>
            )
          })}
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="wh-ooo">{t('settings.out_of_office_message')}</Label>
          <Input
            id="wh-ooo"
            placeholder={t('settings.we_re_back_at_9am_leave')}
            value={message}
            disabled={!canManage}
            onChange={(e) => setMessage(e.target.value)}
          />
        </div>

        {invalid ? (
          <p className="text-xs text-destructive">
            {t('settings.each_open_day_needs_a_closing')}
          </p>
        ) : null}

        {canManage ? (
          <Button onClick={submit} disabled={invalid || save.isPending}>
            {t('settings.save_working_hours')}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}
