/**
 * 3-step email inbox wizard (docs/INTEGRATIONS-CONTRACTS.md, W11):
 *   1. pick a transport — Gmail/Microsoft one-click, SMTP/IMAP, ESPs, forwarding-only
 *   2. address + transport fields (secrets write-only, hosts read-only for one-click)
 *   3. finish — copyable forward-to + inbound webhook URL + IMAP polling
 * Drives both the ChannelsPanel "New channel" flow (create) and the existing
 * inbox configure dialog (edit). Config/secrets shapes mirror BE-B's inbox
 * config contract exactly.
 */

import { Check, Copy, Link2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
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
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

import type { Inbox } from '../api'
import { useCreateInbox, useIntegrations, useUpdateInbox } from '../hooks'
import { ConnectionSelect } from './integrations/ConnectionSelect'
import { mcpOrigin as apiOrigin } from './McpSnippets'
import { t } from '@/i18n'

type TransportId =
  | 'gmail'
  | 'microsoft'
  | 'smtp'
  | 'ses'
  | 'resend'
  | 'postmark'
  | 'sendgrid'
  | 'mailgun'
  | 'global'

interface TransportDef {
  id: TransportId
  label: string
  desc: string
  /** Integrations provider whose connection this transport rides on. */
  oneClickProvider?: 'google' | 'microsoft'
}

const TRANSPORTS: TransportDef[] = [
  {
    id: 'gmail',
    label: 'Gmail',
    desc: 'Send & receive through a connected Google account.',
    oneClickProvider: 'google',
  },
  {
    id: 'microsoft',
    label: 'Microsoft 365',
    desc: 'Send & receive through a connected Microsoft account.',
    oneClickProvider: 'microsoft',
  },
  { id: 'smtp', label: 'SMTP / IMAP', desc: 'Any mailbox with SMTP sending and IMAP polling.' },
  { id: 'ses', label: 'Amazon SES', desc: 'Send via SESv2; receive via an SNS webhook.' },
  { id: 'resend', label: 'Resend', desc: 'Send via the Resend API; receive via its webhook.' },
  { id: 'postmark', label: 'Postmark', desc: 'Send via Postmark; receive via its webhook.' },
  { id: 'sendgrid', label: 'SendGrid', desc: 'Send via SendGrid; receive via Inbound Parse.' },
  { id: 'mailgun', label: 'Mailgun', desc: 'Send via Mailgun; receive via a receiving route.' },
  {
    id: 'global',
    label: 'Forwarding only',
    desc: 'Receive forwarded mail; replies use the instance-wide sender.',
  },
]

/** Transports whose inbound arrives on a provider-specific webhook endpoint. */
const ESP_TRANSPORTS: TransportId[] = ['ses', 'resend', 'postmark', 'sendgrid', 'mailgun']
/** Transports where IMAP polling applies. */
const IMAP_TRANSPORTS: TransportId[] = ['smtp', 'gmail', 'microsoft']

/** Read-only provider hosts (the backend sets these; shown so users trust it). */
const ONE_CLICK_HOSTS: Record<string, { smtp: string; imap: string }> = {
  gmail: { smtp: 'smtp.gmail.com:587 (STARTTLS)', imap: 'imap.gmail.com:993' },
  microsoft: { smtp: 'smtp.office365.com:587 (STARTTLS)', imap: 'outlook.office365.com:993' },
}

interface SecretFieldDef {
  key: string
  label: string
  required?: boolean
}

const SECRET_FIELDS: Record<TransportId, SecretFieldDef[]> = {
  gmail: [],
  microsoft: [],
  global: [],
  smtp: [{ key: 'smtp_password', label: 'SMTP password', required: true }],
  ses: [
    { key: 'ses_access_key_id', label: 'Access key ID', required: true },
    { key: 'ses_secret_access_key', label: 'Secret access key', required: true },
  ],
  resend: [
    { key: 'resend_api_key', label: 'API key', required: true },
    { key: 'resend_webhook_secret', label: 'Inbound webhook secret (optional)' },
  ],
  postmark: [{ key: 'postmark_server_token', label: 'Server token', required: true }],
  sendgrid: [{ key: 'sendgrid_api_key', label: 'API key', required: true }],
  mailgun: [
    { key: 'mailgun_api_key', label: 'API key', required: true },
    { key: 'mailgun_signing_key', label: 'Webhook signing key (optional)' },
  ],
}

/** One-line "what to do with this URL" per ESP. */
const WEBHOOK_HINTS: Record<string, string> = {
  ses: "Point your SES receipt rule's SNS topic here — the subscription confirmation is handled automatically.",
  resend: 'Add this URL as an inbound webhook endpoint in Resend.',
  postmark: 'Set this as the inbound webhook URL on your Postmark server.',
  sendgrid: 'Set this as the destination URL of your Inbound Parse host.',
  mailgun: 'Create a receiving route that forwards messages to this URL.',
}

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  } catch {
    toast.error(t('common.could_not_copy'))
  }
}

function CopyRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="grid gap-1 rounded-md border bg-muted/50 p-3">
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Link2 className="size-3.5" /> {label}
      </span>
      <div className="flex items-center gap-2">
        <code className="flex-1 overflow-x-auto whitespace-nowrap text-xs">{value}</code>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          aria-label={`Copy ${label.toLowerCase()}`}
          onClick={() => copyText(value, label)}
        >
          <Copy className="size-4" />
        </Button>
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

const str = (value: unknown): string =>
  typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''

export function EmailInboxWizard({
  open,
  onOpenChange,
  inbox = null,
  defaultName = 'Email',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Edit mode when set; create mode otherwise. */
  inbox?: Inbox | null
  /** Create mode: the name the new inbox is created with. */
  defaultName?: string
}) {
  const createInbox = useCreateInbox()
  const updateInbox = useUpdateInbox()
  const integrations = useIntegrations()

  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [transport, setTransport] = useState<TransportId>('global')
  const [connectionId, setConnectionId] = useState('')
  const [address, setAddress] = useState('')
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState('587')
  const [smtpUsername, setSmtpUsername] = useState('')
  const [smtpSecurity, setSmtpSecurity] = useState('starttls')
  const [sesRegion, setSesRegion] = useState('')
  const [mailgunDomain, setMailgunDomain] = useState('')
  const [mailgunBase, setMailgunBase] = useState('us')
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  // IMAP polling (step 3) — smtp fields are read-only for gmail/microsoft.
  const [imapEnabled, setImapEnabled] = useState(false)
  const [imapHost, setImapHost] = useState('')
  const [imapPort, setImapPort] = useState('993')
  const [imapUsername, setImapUsername] = useState('')
  const [imapPollMinutes, setImapPollMinutes] = useState('3')
  const [imapPassword, setImapPassword] = useState('')
  const [imapDirty, setImapDirty] = useState(false)
  const [savedInbox, setSavedInbox] = useState<Inbox | null>(null)

  // Seed from the inbox each time the wizard opens (edit mode).
  useEffect(() => {
    if (!open) return
    const config = (inbox?.config ?? {}) as Record<string, unknown>
    const smtp = (config.smtp ?? {}) as Record<string, unknown>
    const imap = (config.imap ?? {}) as Record<string, unknown>
    setStep(1)
    setTransport((str(config.transport) as TransportId) || 'global')
    setConnectionId(str(config.connection_id))
    setAddress(str(config.address))
    setSmtpHost(str(smtp.host))
    setSmtpPort(str(smtp.port) || '587')
    setSmtpUsername(str(smtp.username))
    setSmtpSecurity(str(smtp.security) || 'starttls')
    setSesRegion(str((config.ses as Record<string, unknown> | undefined)?.region))
    setMailgunDomain(str((config.mailgun as Record<string, unknown> | undefined)?.domain))
    setMailgunBase(str((config.mailgun as Record<string, unknown> | undefined)?.base) || 'us')
    setSecrets({})
    setImapEnabled(imap.enabled === true)
    setImapHost(str(imap.host))
    setImapPort(str(imap.port) || '993')
    setImapUsername(str(imap.username))
    setImapPollMinutes(str(imap.poll_minutes) || '3')
    setImapPassword('')
    setImapDirty(false)
    setSavedInbox(inbox ?? null)
  }, [open, inbox])

  const editing = inbox !== null
  const hasStoredSecrets = Boolean(inbox?.has_secrets)

  const connectionsFor = (providerId: string) =>
    (integrations.data?.providers.find((p) => p.id === providerId)?.connections ?? []).filter(
      (c) => c.status !== 'revoked'
    )

  // --- step 2 validation -----------------------------------------------------
  const secretsOk = SECRET_FIELDS[transport].every(
    (f) => !f.required || hasStoredSecrets || (secrets[f.key] ?? '').trim() !== ''
  )
  const fieldsOk =
    address.trim() !== '' &&
    (transport === 'gmail' || transport === 'microsoft'
      ? connectionId !== ''
      : transport === 'smtp'
        ? smtpHost.trim() !== '' && smtpUsername.trim() !== ''
        : transport === 'ses'
          ? sesRegion.trim() !== ''
          : transport === 'mailgun'
            ? mailgunDomain.trim() !== ''
            : true)
  const canSave = fieldsOk && secretsOk

  function buildConfig(): Record<string, unknown> {
    const config = { ...((inbox?.config ?? {}) as Record<string, unknown>) }
    // Sub-objects owned by other transports would trip their validators.
    delete config.smtp
    delete config.ses
    delete config.mailgun
    delete config.connection_id
    config.address = address.trim()
    config.transport = transport
    if (transport === 'gmail' || transport === 'microsoft') config.connection_id = connectionId
    if (transport === 'smtp') {
      config.smtp = {
        host: smtpHost.trim(),
        port: Number(smtpPort) || 587,
        username: smtpUsername.trim(),
        security: smtpSecurity,
      }
    }
    if (transport === 'ses') config.ses = { region: sesRegion.trim() }
    if (transport === 'mailgun') {
      config.mailgun = { domain: mailgunDomain.trim(), base: mailgunBase }
    }
    return config
  }

  function typedSecrets(): Record<string, string> | undefined {
    const typed: Record<string, string> = {}
    for (const field of SECRET_FIELDS[transport]) {
      const value = (secrets[field.key] ?? '').trim()
      if (value !== '') typed[field.key] = value
    }
    return Object.keys(typed).length > 0 ? typed : undefined
  }

  async function save() {
    const config = buildConfig()
    const secretsBody = typedSecrets()
    try {
      const saved = editing
        ? await updateInbox.mutateAsync({
            id: inbox.id,
            body: { config, ...(secretsBody ? { secrets: secretsBody } : {}) },
          })
        : await createInbox.mutateAsync({
            name: defaultName,
            channel_type: 'email',
            enabled: true,
            config,
            ...(secretsBody ? { secrets: secretsBody } : {}),
          })
      setSavedInbox(saved)
      // The backend auto-fills IMAP for gmail/microsoft — reflect what it stored.
      const imap = ((saved.config as Record<string, unknown>)?.imap ?? {}) as Record<
        string,
        unknown
      >
      setImapEnabled(imap.enabled === true)
      if (str(imap.host)) setImapHost(str(imap.host))
      if (str(imap.port)) setImapPort(str(imap.port))
      if (str(imap.username)) setImapUsername(str(imap.username))
      if (str(imap.poll_minutes)) setImapPollMinutes(str(imap.poll_minutes))
      setImapDirty(false)
      setStep(3)
    } catch {
      /* toast handled in hook */
    }
  }

  async function finish() {
    if (imapDirty && savedInbox && IMAP_TRANSPORTS.includes(transport)) {
      const config = { ...(savedInbox.config as Record<string, unknown>) }
      const existing = (config.imap ?? {}) as Record<string, unknown>
      const pollMinutes = Math.max(2, Math.round(Number(imapPollMinutes) || 3))
      config.imap = {
        ...existing,
        enabled: imapEnabled,
        poll_minutes: pollMinutes,
        ...(transport === 'smtp'
          ? {
              host: imapHost.trim(),
              port: Number(imapPort) || 993,
              username: imapUsername.trim(),
            }
          : {}),
      }
      const secretsBody =
        transport === 'smtp' && imapPassword.trim() !== ''
          ? { secrets: { imap_password: imapPassword.trim() } }
          : {}
      try {
        await updateInbox.mutateAsync({ id: savedInbox.id, body: { config, ...secretsBody } })
      } catch {
        return // keep the wizard open so nothing is silently lost
      }
    }
    onOpenChange(false)
  }

  const savedConfig = (savedInbox?.config ?? {}) as Record<string, unknown>
  const forwardTo = str(savedConfig.forward_to)
  const webhookToken = str(savedConfig.webhook_token)
  const webhookUrl =
    savedInbox && webhookToken
      ? ESP_TRANSPORTS.includes(transport)
        ? `${apiOrigin()}/api/channels/email/inbound/${transport}/${savedInbox.id}/${webhookToken}`
        : `${apiOrigin()}/api/channels/email/inbound/${savedInbox.id}/${webhookToken}`
      : null

  const saving = createInbox.isPending || updateInbox.isPending

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? `Configure “${inbox.name}”` : 'New email channel'}</DialogTitle>
          <DialogDescription>
            Step {step} of 3 —{' '}
            {step === 1
              ? 'choose how this inbox sends and receives mail.'
              : step === 2
                ? 'connection details. Secrets are stored encrypted and never shown again.'
                : 'wire up inbound mail.'}
          </DialogDescription>
        </DialogHeader>

        {step === 1 ? (
          <div className="grid gap-2 py-2 sm:grid-cols-2">
            {TRANSPORTS.map((transportDef) => {
              const needsConnection =
                transportDef.oneClickProvider !== undefined && connectionsFor(transportDef.oneClickProvider).length === 0
              const selected = transport === transportDef.id
              if (needsConnection) {
                return (
                  <div key={transportDef.id} className="grid gap-2 rounded-md border border-dashed p-3">
                    <div>
                      <span className="flex items-center gap-1.5 text-sm font-medium">
                        {transportDef.label}
                        <Badge variant="outline" className="text-[10px]">
                          {t('settings.one_click')}
                        </Badge>
                      </span>
                      <p className="mt-0.5 text-xs text-muted-foreground">{transportDef.desc}</p>
                    </div>
                    <Link
                      to="/settings/integrations"
                      className="text-xs font-medium underline underline-offset-2 hover:text-foreground"
                      onClick={() => onOpenChange(false)}
                    >
                      Connect {transportDef.oneClickProvider === 'google' ? 'Google' : 'Microsoft'} first
                    </Link>
                  </div>
                )
              }
              return (
                <button
                  key={transportDef.id}
                  type="button"
                  aria-pressed={selected}
                  className={cn(
                    'rounded-md border p-3 text-left transition-colors hover:bg-accent/50',
                    selected && 'border-primary bg-accent/30'
                  )}
                  onClick={() => {
                    setTransport(transportDef.id)
                    setStep(2)
                  }}
                >
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    {transportDef.label}
                    {transportDef.oneClickProvider ? (
                      <Badge variant="outline" className="text-[10px]">
                        {t('settings.one_click')}
                      </Badge>
                    ) : null}
                    {selected ? <Check className="size-3.5 text-primary" /> : null}
                  </span>
                  <p className="mt-0.5 text-xs text-muted-foreground">{transportDef.desc}</p>
                </button>
              )
            })}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="grid gap-4 py-2">
            {transport === 'gmail' || transport === 'microsoft' ? (
              <ConnectionSelect
                id="email-connection"
                providerId={transport === 'gmail' ? 'google' : 'microsoft'}
                label={transport === 'gmail' ? 'Google account' : 'Microsoft account'}
                value={connectionId}
                onChange={(id) => {
                  setConnectionId(id)
                  if (!address.trim()) {
                    const conn = connectionsFor(
                      transport === 'gmail' ? 'google' : 'microsoft'
                    ).find((c) => c.id === id)
                    if (conn?.account_label) setAddress(conn.account_label)
                  }
                }}
              />
            ) : null}

            <div className="grid gap-1.5">
              <Label htmlFor="email-address">
                {t('settings.address')}<span className="text-destructive"> *</span>
              </Label>
              <Input
                id="email-address"
                placeholder={t('settings.support_yourcompany_com')}
                value={address}
                onChange={(e) => setAddress(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                The public address customers write to; outbound mail is sent from it.
              </p>
            </div>

            {transport === 'gmail' || transport === 'microsoft' ? (
              <div className="grid gap-1 rounded-md border bg-muted/50 p-3 text-xs text-muted-foreground">
                <span>SMTP: {ONE_CLICK_HOSTS[transport].smtp}</span>
                <span>IMAP: {ONE_CLICK_HOSTS[transport].imap}</span>
                <span>{t('settings.set_automatically_sign_in_uses_the')}</span>
              </div>
            ) : null}

            {transport === 'smtp' ? (
              <>
                <div className="grid grid-cols-[1fr_auto] gap-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor="smtp-host">
                      {t('settings.smtp_host')}<span className="text-destructive"> *</span>
                    </Label>
                    <Input
                      id="smtp-host"
                      placeholder={t('settings.mail_example_com')}
                      value={smtpHost}
                      onChange={(e) => setSmtpHost(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="smtp-port">{t('settings.port')}</Label>
                    <Input
                      id="smtp-port"
                      type="number"
                      className="w-24"
                      value={smtpPort}
                      onChange={(e) => setSmtpPort(e.target.value)}
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="grid gap-1.5">
                    <Label htmlFor="smtp-username">
                      {t('settings.username')}<span className="text-destructive"> *</span>
                    </Label>
                    <Input
                      id="smtp-username"
                      value={smtpUsername}
                      onChange={(e) => setSmtpUsername(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label htmlFor="smtp-security">{t('settings.security')}</Label>
                    <NativeSelect
                      id="smtp-security"
                      className="w-full"
                      value={smtpSecurity}
                      onChange={(e) => setSmtpSecurity(e.target.value)}
                    >
                      <NativeSelectOption value="starttls">STARTTLS</NativeSelectOption>
                      <NativeSelectOption value="tls">TLS</NativeSelectOption>
                      <NativeSelectOption value="none">{t('common.none')}</NativeSelectOption>
                    </NativeSelect>
                  </div>
                </div>
              </>
            ) : null}

            {transport === 'ses' ? (
              <div className="grid gap-1.5">
                <Label htmlFor="ses-region">
                  {t('settings.aws_region')}<span className="text-destructive"> *</span>
                </Label>
                <Input
                  id="ses-region"
                  placeholder="eu-central-1"
                  value={sesRegion}
                  onChange={(e) => setSesRegion(e.target.value)}
                />
              </div>
            ) : null}

            {transport === 'mailgun' ? (
              <div className="grid grid-cols-[1fr_auto] gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="mailgun-domain">
                    {t('settings.sending_domain')}<span className="text-destructive"> *</span>
                  </Label>
                  <Input
                    id="mailgun-domain"
                    placeholder={t('settings.mg_example_com')}
                    value={mailgunDomain}
                    onChange={(e) => setMailgunDomain(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="mailgun-base">{t('settings.region')}</Label>
                  <NativeSelect
                    id="mailgun-base"
                    value={mailgunBase}
                    onChange={(e) => setMailgunBase(e.target.value)}
                  >
                    <NativeSelectOption value="us">US</NativeSelectOption>
                    <NativeSelectOption value="eu">EU</NativeSelectOption>
                  </NativeSelect>
                </div>
              </div>
            ) : null}

            {SECRET_FIELDS[transport].map((field) => (
              <div key={field.key} className="grid gap-1.5">
                <Label htmlFor={`email-secret-${field.key}`}>
                  {field.label}
                  {field.required ? <span className="text-destructive"> *</span> : null}
                </Label>
                <Input
                  id={`email-secret-${field.key}`}
                  type="password"
                  autoComplete="off"
                  placeholder={hasStoredSecrets ? 'unchanged' : undefined}
                  value={secrets[field.key] ?? ''}
                  onChange={(e) =>
                    setSecrets((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                />
              </div>
            ))}
          </div>
        ) : null}

        {step === 3 ? (
          <div className="grid gap-4 py-2">
            {forwardTo ? (
              <CopyRow
                label={t('settings.forward_to_address')}
                value={forwardTo}
                hint={`Set up forwarding from ${str(savedConfig.address) || 'your address'} to this address at your email provider.`}
              />
            ) : null}

            {webhookUrl ? (
              <CopyRow
                label={t('settings.inbound_webhook_url')}
                value={webhookUrl}
                hint={WEBHOOK_HINTS[transport] ?? 'POST inbound email JSON to this URL.'}
              />
            ) : null}

            {!forwardTo && !webhookUrl ? (
              <p className="text-sm text-muted-foreground">
                {t('settings.inbound_endpoints_appear_here_once_the')}
              </p>
            ) : null}

            {IMAP_TRANSPORTS.includes(transport) ? (
              <div className="grid gap-3 rounded-md border p-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="grid gap-1">
                    <Label htmlFor="imap-enabled">{t('settings.imap_polling')}</Label>
                    <p className="text-xs text-muted-foreground">
                      {t('settings.pull_new_mail_directly_from_the')}
                    </p>
                  </div>
                  <Switch
                    id="imap-enabled"
                    checked={imapEnabled}
                    onCheckedChange={(checked) => {
                      setImapEnabled(checked)
                      setImapDirty(true)
                    }}
                  />
                </div>

                {imapEnabled && transport === 'smtp' ? (
                  <>
                    <div className="grid grid-cols-[1fr_auto] gap-3">
                      <div className="grid gap-1.5">
                        <Label htmlFor="imap-host">{t('settings.imap_host')}</Label>
                        <Input
                          id="imap-host"
                          placeholder={t('settings.imap_example_com')}
                          value={imapHost}
                          onChange={(e) => {
                            setImapHost(e.target.value)
                            setImapDirty(true)
                          }}
                        />
                      </div>
                      <div className="grid gap-1.5">
                        <Label htmlFor="imap-port">{t('settings.port')}</Label>
                        <Input
                          id="imap-port"
                          type="number"
                          className="w-24"
                          value={imapPort}
                          onChange={(e) => {
                            setImapPort(e.target.value)
                            setImapDirty(true)
                          }}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="grid gap-1.5">
                        <Label htmlFor="imap-username">{t('settings.imap_username')}</Label>
                        <Input
                          id="imap-username"
                          value={imapUsername}
                          onChange={(e) => {
                            setImapUsername(e.target.value)
                            setImapDirty(true)
                          }}
                        />
                      </div>
                      <div className="grid gap-1.5">
                        <Label htmlFor="imap-password">{t('settings.imap_password')}</Label>
                        <Input
                          id="imap-password"
                          type="password"
                          autoComplete="off"
                          placeholder={hasStoredSecrets ? 'unchanged' : undefined}
                          value={imapPassword}
                          onChange={(e) => {
                            setImapPassword(e.target.value)
                            setImapDirty(true)
                          }}
                        />
                      </div>
                    </div>
                  </>
                ) : null}

                {imapEnabled && transport !== 'smtp' ? (
                  <p className="text-xs text-muted-foreground">
                    Host {ONE_CLICK_HOSTS[transport]?.imap} — sign-in uses the connected account.
                  </p>
                ) : null}

                {imapEnabled ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="imap-poll-minutes">Poll every N minutes (min 2)</Label>
                    <Input
                      id="imap-poll-minutes"
                      type="number"
                      min={2}
                      className="w-28"
                      value={imapPollMinutes}
                      onChange={(e) => {
                        setImapPollMinutes(e.target.value)
                        setImapDirty(true)
                      }}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          {step === 1 ? (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.cancel')}
            </Button>
          ) : step === 2 ? (
            <Button variant="outline" onClick={() => setStep(1)}>
              {t('common.back')}
            </Button>
          ) : null}
          {step === 1 ? (
            <Button onClick={() => setStep(2)}>{t('settings.continue')}</Button>
          ) : step === 2 ? (
            <Button onClick={save} disabled={!canSave || saving}>
              {saving ? 'Saving…' : editing ? 'Save' : 'Create inbox'}
            </Button>
          ) : (
            <Button onClick={finish} disabled={updateInbox.isPending}>
              {updateInbox.isPending ? 'Saving…' : 'Done'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
