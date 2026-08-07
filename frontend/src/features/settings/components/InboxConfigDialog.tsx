/**
 * Per-channel-type configuration dialog for an inbox. Config fields prefill from
 * inbox.config; secret fields are write-only — always blank, with an "unchanged"
 * placeholder once the inbox has stored secrets. The PATCH body includes `secrets`
 * only when the user actually typed one.
 */

import { Copy, Link2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useAgents } from '@/features/ai/hooks'

import type { Inbox, InboxUpdate } from '../api'
import { useUpdateInbox } from '../hooks'

/** Radix Select forbids an empty-string item value, so "none" needs a sentinel. */
const NO_AGENT = '__none__'

interface FieldDef {
  key: string
  label: string
  required?: boolean
  placeholder?: string
  /** Defaults to 'text'. Non-text kinds render a dedicated control. */
  type?: 'text' | 'color' | 'boolean' | 'agent'
  /** Seed used when the inbox config has no value for this key yet. */
  defaultValue?: string
  help?: string
}

interface HintDef {
  label: string
  path: (inboxId: string, config: Record<string, string>) => string
}

export interface ChannelConfigSpec {
  config: FieldDef[]
  secrets: FieldDef[]
  hints: HintDef[]
}

/** Which fields each channel type needs (mirrors backend channel adapters). */
export const CHANNEL_CONFIG_SPECS: Record<string, ChannelConfigSpec> = {
  // The widget already honours these server-side (DEFAULT_WIDGET_CONFIG); they
  // just had no UI, so the launcher colour, greeting and triage behaviour were
  // unreachable once an inbox existed.
  widget: {
    config: [
      {
        key: 'ai_agent_id',
        label: 'AI agent',
        type: 'agent',
        help: 'A live agent answers new conversations on this channel before a teammate picks them up. The engine already keys off this — until it is set, agents never see real traffic.',
      },
      {
        key: 'greeting',
        label: 'Greeting',
        placeholder: 'Hi! How can we help?',
        defaultValue: 'Hi! How can we help?',
      },
      { key: 'accent_color', label: 'Accent colour', type: 'color', defaultValue: '#6366f1' },
      {
        key: 'auto_assign',
        label: 'Auto-assign new conversations',
        type: 'boolean',
        defaultValue: 'false',
        help: 'Off means new conversations land in Unassigned for the team to triage. On round-robins them to the member with the lightest open load.',
      },
    ],
    secrets: [],
    hints: [],
  },
  whatsapp: {
    config: [
      { key: 'phone_number_id', label: 'Phone number ID', required: true },
      { key: 'business_account_id', label: 'Business account ID' },
      { key: 'webhook_verify_token', label: 'Webhook verify token', required: true },
    ],
    secrets: [
      { key: 'api_key', label: 'Access token', required: true },
      { key: 'app_secret', label: 'App secret' },
    ],
    hints: [{ label: 'Webhook path', path: (id) => `/api/channels/whatsapp/webhook/${id}` }],
  },
  messenger: {
    config: [
      { key: 'page_id', label: 'Page ID' },
      { key: 'webhook_verify_token', label: 'Webhook verify token', required: true },
    ],
    secrets: [
      { key: 'page_access_token', label: 'Page access token', required: true },
      { key: 'app_secret', label: 'App secret' },
    ],
    hints: [{ label: 'Webhook path', path: (id) => `/api/channels/messenger/webhook/${id}` }],
  },
  instagram: {
    config: [
      { key: 'instagram_id', label: 'Instagram ID' },
      { key: 'webhook_verify_token', label: 'Webhook verify token', required: true },
    ],
    secrets: [
      { key: 'access_token', label: 'Access token', required: true },
      { key: 'app_secret', label: 'App secret' },
    ],
    hints: [{ label: 'Webhook path', path: (id) => `/api/channels/instagram/webhook/${id}` }],
  },
  sms: {
    config: [
      {
        key: 'phone_number',
        label: 'Phone number (E.164)',
        required: true,
        placeholder: '+15551234567',
      },
    ],
    secrets: [
      { key: 'account_sid', label: 'Account SID', required: true },
      { key: 'auth_token', label: 'Auth token', required: true },
    ],
    hints: [
      { label: 'Webhook path', path: (id) => `/api/channels/sms/webhook/${id}` },
      { label: 'Status callback path', path: (id) => `/api/channels/sms/status/${id}` },
    ],
  },
  line: {
    config: [],
    secrets: [
      { key: 'channel_secret', label: 'Channel secret', required: true },
      { key: 'channel_token', label: 'Channel token', required: true },
    ],
    hints: [{ label: 'Webhook path', path: (id) => `/api/channels/line/webhook/${id}` }],
  },
  telegram: {
    config: [{ key: 'webhook_secret', label: 'Webhook secret' }],
    secrets: [{ key: 'bot_token', label: 'Bot token' }],
    hints: [
      {
        label: 'Webhook path',
        path: (id, config) =>
          `/api/channels/telegram/webhook/${id}?secret=${config.webhook_secret?.trim() || '...'}`,
      },
    ],
  },
  slack: {
    config: [],
    secrets: [
      { key: 'bot_token', label: 'Bot token' },
      { key: 'signing_secret', label: 'Signing secret' },
    ],
    hints: [{ label: 'Events path', path: () => '/api/channels/slack/events' }],
  },
  email: {
    config: [{ key: 'address', label: 'Address', placeholder: 'support@yourcompany.com' }],
    secrets: [],
    hints: [{ label: 'Inbound POST path', path: () => '/api/channels/email/inbound' }],
  },
}

async function copy(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  } catch {
    toast.error('Could not copy')
  }
}

export function InboxConfigDialog({
  inbox,
  open,
  onOpenChange,
}: {
  inbox: Inbox | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const updateInbox = useUpdateInbox()
  const [configValues, setConfigValues] = useState<Record<string, string>>({})
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const { data: agents = [] } = useAgents()
  // Only a live agent can pick up traffic — the engine ignores draft/off ones.
  const liveAgents = agents.filter((a) => a.status === 'live')

  const spec = inbox ? CHANNEL_CONFIG_SPECS[inbox.channel_type] : undefined

  useEffect(() => {
    if (!open || !inbox) return
    const seeded: Record<string, string> = {}
    for (const field of CHANNEL_CONFIG_SPECS[inbox.channel_type]?.config ?? []) {
      const value = (inbox.config as Record<string, unknown>)?.[field.key]
      seeded[field.key] = value == null ? (field.defaultValue ?? '') : String(value)
    }
    setConfigValues(seeded)
    setSecretValues({})
  }, [open, inbox])

  if (!inbox || !spec) return null

  const requiredConfigOk = spec.config.every(
    (f) => !f.required || (configValues[f.key] ?? '').trim() !== ''
  )
  // Required secrets only block the first save; afterwards blank means "keep".
  const requiredSecretsOk =
    inbox.has_secrets || spec.secrets.every((f) => !f.required || (secretValues[f.key] ?? '').trim() !== '')
  const canSave = requiredConfigOk && requiredSecretsOk

  async function save() {
    if (!inbox || !spec) return
    const config: Record<string, unknown> = { ...(inbox.config as Record<string, unknown>) }
    for (const field of spec.config) {
      const raw = configValues[field.key] ?? ''
      if (field.type === 'boolean') {
        // Always write booleans — "off" is a real value, not an absent one.
        config[field.key] = raw === 'true'
        continue
      }
      const value = raw.trim()
      if (value === '') delete config[field.key]
      else config[field.key] = value
    }
    const secrets: Record<string, string> = {}
    for (const field of spec.secrets) {
      const value = (secretValues[field.key] ?? '').trim()
      if (value !== '') secrets[field.key] = value
    }
    const body: InboxUpdate = {}
    if (spec.config.length > 0) body.config = config
    if (Object.keys(secrets).length > 0) body.secrets = secrets
    try {
      await updateInbox.mutateAsync({ id: inbox.id, body })
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Configure “{inbox.name}”</DialogTitle>
          <DialogDescription>
            Connection settings for this <span className="capitalize">{inbox.channel_type}</span>{' '}
            channel. Secrets are stored encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          {spec.config.map((field) =>
            field.type === 'agent' ? (
              <div key={field.key} className="grid gap-1.5">
                <Label htmlFor={`cfg-${field.key}`}>{field.label}</Label>
                <Select
                  value={configValues[field.key] || NO_AGENT}
                  onValueChange={(v) =>
                    setConfigValues((prev) => ({ ...prev, [field.key]: v === NO_AGENT ? '' : v }))
                  }
                >
                  <SelectTrigger id={`cfg-${field.key}`} className="w-full">
                    <SelectValue placeholder="No AI agent" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_AGENT}>No AI agent</SelectItem>
                    {liveAgents.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.avatar_emoji ? `${a.avatar_emoji} ` : ''}
                        {a.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {field.help ? <p className="text-xs text-muted-foreground">{field.help}</p> : null}
                {liveAgents.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No live agents yet — set an agent to “Live” under AI Agents first.
                  </p>
                ) : null}
              </div>
            ) : field.type === 'boolean' ? (
              <div key={field.key} className="flex items-start justify-between gap-4">
                <div className="grid gap-1">
                  <Label htmlFor={`cfg-${field.key}`}>{field.label}</Label>
                  {field.help ? (
                    <p className="text-xs text-muted-foreground">{field.help}</p>
                  ) : null}
                </div>
                <Switch
                  id={`cfg-${field.key}`}
                  checked={configValues[field.key] === 'true'}
                  onCheckedChange={(checked) =>
                    setConfigValues((prev) => ({ ...prev, [field.key]: checked ? 'true' : 'false' }))
                  }
                />
              </div>
            ) : (
              <div key={field.key} className="grid gap-1.5">
                <Label htmlFor={`cfg-${field.key}`}>
                  {field.label}
                  {field.required ? <span className="text-destructive"> *</span> : null}
                </Label>
                <div className="flex items-center gap-2">
                  {field.type === 'color' ? (
                    <Input
                      type="color"
                      aria-label={`${field.label} swatch`}
                      className="h-9 w-14 shrink-0 p-1"
                      value={configValues[field.key] || field.defaultValue || '#6366f1'}
                      onChange={(e) =>
                        setConfigValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                      }
                    />
                  ) : null}
                  <Input
                    id={`cfg-${field.key}`}
                    placeholder={field.placeholder}
                    value={configValues[field.key] ?? ''}
                    onChange={(e) =>
                      setConfigValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                    }
                  />
                </div>
                {field.help ? <p className="text-xs text-muted-foreground">{field.help}</p> : null}
              </div>
            )
          )}

          {spec.secrets.map((field) => (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={`sec-${field.key}`}>
                {field.label}
                {field.required ? <span className="text-destructive"> *</span> : null}
              </Label>
              <Input
                id={`sec-${field.key}`}
                type="password"
                autoComplete="off"
                placeholder={inbox.has_secrets ? 'unchanged' : field.placeholder}
                value={secretValues[field.key] ?? ''}
                onChange={(e) =>
                  setSecretValues((prev) => ({ ...prev, [field.key]: e.target.value }))
                }
              />
            </div>
          ))}

          {spec.hints.length > 0 ? (
            <div className="grid gap-2 rounded-md border bg-muted/50 p-3">
              {spec.hints.map((hint) => {
                const path = hint.path(inbox.id, configValues)
                return (
                  <div key={hint.label} className="grid gap-1">
                    <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Link2 className="size-3.5" /> {hint.label}
                    </span>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 overflow-x-auto whitespace-nowrap text-xs">
                        {path}
                      </code>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0"
                        aria-label={`Copy ${hint.label.toLowerCase()}`}
                        onClick={() => copy(path, hint.label)}
                      >
                        <Copy className="size-4" />
                      </Button>
                    </div>
                  </div>
                )
              })}
              <p className="text-xs text-muted-foreground">
                Point the provider at this path on your Stept host.
              </p>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || updateInbox.isPending}>
            {updateInbox.isPending ? 'Saving…' : 'Save configuration'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
