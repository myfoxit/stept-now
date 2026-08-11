import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

import type { Webhook } from '../api'
import { WEBHOOK_EVENTS } from '../constants'
import { useCreateWebhook, useUpdateWebhook } from '../hooks'
import { t } from '@/i18n'

export function WebhookEditorDialog({
  open,
  onOpenChange,
  webhook,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  webhook?: Webhook | null
}) {
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [events, setEvents] = useState<string[]>(['*'])
  const [enabled, setEnabled] = useState(true)

  const create = useCreateWebhook()
  const update = useUpdateWebhook()
  const saving = create.isPending || update.isPending

  useEffect(() => {
    if (!open) return
    if (webhook) {
      setUrl(webhook.url)
      setDescription(webhook.description ?? '')
      setEvents(webhook.events.length > 0 ? webhook.events : ['*'])
      setEnabled(webhook.enabled)
    } else {
      setUrl('')
      setDescription('')
      setEvents(['*'])
      setEnabled(true)
    }
  }, [open, webhook])

  function toggleEvent(name: string) {
    setEvents((prev) => (prev.includes(name) ? prev.filter((e) => e !== name) : [...prev, name]))
  }

  const validUrl = /^https?:\/\//.test(url.trim())
  const canSave = validUrl && events.length > 0

  async function save() {
    const body = { url: url.trim(), events, enabled, description: description.trim() || null }
    try {
      if (webhook) await update.mutateAsync({ id: webhook.id, body })
      else await create.mutateAsync(body)
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{webhook ? 'Edit webhook' : 'New webhook'}</DialogTitle>
          <DialogDescription>
            {t('automation.stept_posts_a_signed_json_payload')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="wh-url">{t('common.endpoint_url')}</Label>
            <Input
              id="wh-url"
              placeholder="https://example.com/hooks/stept"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            {url.length > 0 && !validUrl ? (
              <p className="text-xs text-destructive">Enter an http(s) URL.</p>
            ) : null}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="wh-desc">{t('common.description')}</Label>
            <Input
              id="wh-desc"
              placeholder={t('automation.optional_label')}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>{t('automation.events')}</Label>
            <div className="grid max-h-56 grid-cols-1 gap-1.5 overflow-y-auto rounded-md border p-3 sm:grid-cols-2">
              {WEBHOOK_EVENTS.map((name) => (
                <label
                  key={name}
                  className="flex items-center gap-2 text-sm"
                  htmlFor={`wh-event-${name}`}
                >
                  <Checkbox
                    id={`wh-event-${name}`}
                    checked={events.includes(name)}
                    onCheckedChange={() => toggleEvent(name)}
                  />
                  <span className={name === '*' ? 'font-medium' : 'font-mono text-xs'}>
                    {name === '*' ? 'All events (*)' : name}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <Label htmlFor="wh-enabled">{t('common.enabled')}</Label>
            <Switch id="wh-enabled" checked={enabled} onCheckedChange={setEnabled} />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button onClick={save} disabled={!canSave || saving}>
            {saving ? 'Saving…' : webhook ? 'Save changes' : 'Create webhook'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
