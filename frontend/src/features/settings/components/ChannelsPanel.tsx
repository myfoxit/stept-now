import { Code2, Copy, Inbox as InboxIcon, Plus, Settings2, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useHasPerm } from '@/stores/auth'

import type { Inbox, InboxCreate } from '../api'
import { useCreateInbox, useDeleteInbox, useInboxes, useUpdateInbox } from '../hooks'
import { CHANNEL_CONFIG_SPECS, InboxConfigDialog } from './InboxConfigDialog'

const CHANNEL_TYPES = [
  { value: 'widget', label: 'Website widget' },
  { value: 'email', label: 'Email' },
  { value: 'slack', label: 'Slack' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'messenger', label: 'Facebook Messenger' },
  { value: 'instagram', label: 'Instagram' },
  { value: 'sms', label: 'SMS (Twilio)' },
  { value: 'line', label: 'LINE' },
  { value: 'api', label: 'API' },
]

async function copy(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  } catch {
    toast.error('Could not copy')
  }
}

export function ChannelsPanel() {
  const canManage = useHasPerm('channels:manage')
  const inboxes = useInboxes()
  const createInbox = useCreateInbox()
  const updateInbox = useUpdateInbox()
  const deleteInbox = useDeleteInbox()

  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [channelType, setChannelType] = useState('widget')
  const [deleting, setDeleting] = useState<Inbox | null>(null)
  const [configuring, setConfiguring] = useState<Inbox | null>(null)

  async function submit() {
    if (!name.trim()) return
    const body: InboxCreate = {
      name: name.trim(),
      channel_type: channelType as InboxCreate['channel_type'],
      enabled: true,
    }
    try {
      await createInbox.mutateAsync(body)
      setCreateOpen(false)
      setName('')
      setChannelType('widget')
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Connect the channels your customers reach you on.
        </p>
        {canManage ? (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" /> New channel
          </Button>
        ) : null}
      </div>

      {inboxes.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : !inboxes.data || inboxes.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <InboxIcon />
            </EmptyMedia>
            <EmptyTitle>No channels yet</EmptyTitle>
            <EmptyDescription>Add a website widget, email, or messaging channel.</EmptyDescription>
          </EmptyHeader>
          {canManage ? (
            <EmptyContent>
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="size-4" /> Add a channel
              </Button>
            </EmptyContent>
          ) : null}
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {inboxes.data.map((inbox) => (
            <li key={inbox.id}>
              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <CardTitle className="text-sm">{inbox.name}</CardTitle>
                      <Badge variant="secondary" className="capitalize">
                        {inbox.channel_type}
                      </Badge>
                      {inbox.has_secrets ? <Badge variant="outline">Configured</Badge> : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Enabled</span>
                      <Switch
                        checked={inbox.enabled}
                        disabled={!canManage || updateInbox.isPending}
                        aria-label={`Enable ${inbox.name}`}
                        onCheckedChange={(checked) =>
                          updateInbox.mutate({ id: inbox.id, body: { enabled: checked } })
                        }
                      />
                      {canManage && CHANNEL_CONFIG_SPECS[inbox.channel_type] ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Configure ${inbox.name}`}
                          onClick={() => setConfiguring(inbox)}
                        >
                          <Settings2 className="size-4" />
                        </Button>
                      ) : null}
                      {canManage ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete ${inbox.name}`}
                          onClick={() => setDeleting(inbox)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </CardHeader>
                {inbox.embed_snippet ? (
                  <CardContent>
                    <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Code2 className="size-3.5" /> Embed snippet
                    </div>
                    <div className="flex items-start gap-2 rounded-md border bg-muted/50 p-3">
                      <pre className="flex-1 overflow-x-auto text-xs">
                        <code>{inbox.embed_snippet}</code>
                      </pre>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 shrink-0"
                        aria-label="Copy embed snippet"
                        onClick={() => copy(inbox.embed_snippet!, 'Snippet')}
                      >
                        <Copy className="size-4" />
                      </Button>
                    </div>
                  </CardContent>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New channel</DialogTitle>
            <DialogDescription>Create an inbox for a communication channel.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label htmlFor="inbox-name">Name</Label>
              <Input
                id="inbox-name"
                placeholder="e.g. Website chat"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="inbox-type">Channel type</Label>
              <NativeSelect
                id="inbox-type"
                className="w-full"
                value={channelType}
                onChange={(e) => setChannelType(e.target.value)}
              >
                {CHANNEL_TYPES.map((type) => (
                  <NativeSelectOption key={type.value} value={type.value}>
                    {type.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!name.trim() || createInbox.isPending}>
              {createInbox.isPending ? 'Creating…' : 'Create channel'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <InboxConfigDialog
        inbox={configuring}
        open={configuring !== null}
        onOpenChange={(open) => !open && setConfiguring(null)}
      />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Conversations from this channel will stop syncing. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) deleteInbox.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
