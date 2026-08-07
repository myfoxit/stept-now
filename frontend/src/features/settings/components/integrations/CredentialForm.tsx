/**
 * "Use your own app" credential form for one provider: client_id, write-only
 * client_secret, provider extra fields (also write-only), and the read-only
 * redirect URI to register in the provider console.
 */

import { Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import type { IntegrationCredential, IntegrationProvider } from '../../api'
import { useDeleteCredentials, usePutCredentials } from '../../hooks'
import { fieldLabel } from './lib'

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  } catch {
    toast.error('Could not copy')
  }
}

export function CredentialForm({
  provider,
  credential,
}: {
  provider: IntegrationProvider
  credential: IntegrationCredential
}) {
  const put = usePutCredentials()
  const remove = useDeleteCredentials()

  const [clientId, setClientId] = useState(credential.client_id ?? '')
  const [clientSecret, setClientSecret] = useState('')
  const [extra, setExtra] = useState<Record<string, string>>({})

  // Re-seed after a save round-trips through the query cache.
  useEffect(() => {
    setClientId(credential.client_id ?? '')
  }, [credential.client_id])

  const extraFields = credential.fields.filter(
    (f) => f !== 'client_id' && f !== 'client_secret'
  )
  // The secret only blocks the first save; afterwards blank means "keep".
  const canSave =
    clientId.trim() !== '' && (credential.has_secret || clientSecret.trim() !== '')
  // A workspace row exists when a client_id is stored and not just env fallback.
  const hasWorkspaceRow = Boolean(credential.client_id) && !credential.from_env

  async function save() {
    const typedExtra: Record<string, string> = {}
    for (const [key, value] of Object.entries(extra)) {
      if (value.trim() !== '') typedExtra[key] = value.trim()
    }
    try {
      await put.mutateAsync({
        provider: provider.id,
        body: {
          client_id: clientId.trim(),
          // Omitted = keep the stored secret (write-only semantics).
          ...(clientSecret.trim() !== '' ? { client_secret: clientSecret.trim() } : {}),
          ...(Object.keys(typedExtra).length > 0 ? { extra: typedExtra } : {}),
        },
      })
      setClientSecret('')
      setExtra({})
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <div className="grid gap-3 rounded-md border bg-muted/30 p-3">
      {credential.from_env ? (
        <p className="text-xs text-muted-foreground">
          Currently using the instance-level app configured by your server admin. Saving here
          switches this workspace to your own app.
        </p>
      ) : null}

      <div className="grid gap-1.5">
        <Label htmlFor={`cred-${provider.id}-client-id`}>Client ID</Label>
        <Input
          id={`cred-${provider.id}-client-id`}
          value={clientId}
          autoComplete="off"
          onChange={(e) => setClientId(e.target.value)}
        />
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor={`cred-${provider.id}-client-secret`}>Client secret</Label>
        <Input
          id={`cred-${provider.id}-client-secret`}
          type="password"
          autoComplete="off"
          placeholder={credential.has_secret ? 'unchanged' : ''}
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Stored encrypted and never shown again.
        </p>
      </div>

      {extraFields.map((field) => (
        <div key={field} className="grid gap-1.5">
          <Label htmlFor={`cred-${provider.id}-${field}`}>{fieldLabel(field)}</Label>
          <Input
            id={`cred-${provider.id}-${field}`}
            type="password"
            autoComplete="off"
            placeholder={credential.has_secret ? 'unchanged' : ''}
            value={extra[field] ?? ''}
            onChange={(e) => setExtra((prev) => ({ ...prev, [field]: e.target.value }))}
          />
        </div>
      ))}

      <div className="grid gap-1.5">
        <Label htmlFor={`cred-${provider.id}-redirect`}>Redirect URI</Label>
        <div className="flex items-center gap-2">
          <Input
            id={`cred-${provider.id}-redirect`}
            readOnly
            value={credential.redirect_uri}
            className="font-mono text-xs"
          />
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            aria-label="Copy redirect URI"
            onClick={() => copyText(credential.redirect_uri, 'Redirect URI')}
          >
            <Copy className="size-4" />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Register this exact URL in the {provider.name} developer console.
        </p>
      </div>

      <div className="flex items-center justify-end gap-2">
        {hasWorkspaceRow ? (
          <Button
            variant="outline"
            size="sm"
            disabled={remove.isPending}
            onClick={() => remove.mutate(provider.id)}
          >
            Remove
          </Button>
        ) : null}
        <Button size="sm" onClick={save} disabled={!canSave || put.isPending}>
          {put.isPending ? 'Saving…' : 'Save credentials'}
        </Button>
      </div>
    </div>
  )
}
