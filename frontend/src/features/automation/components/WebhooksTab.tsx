import { Copy, Eye, EyeOff, Pencil, Plus, Send, Trash2, Webhook as WebhookIcon } from 'lucide-react'
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
import { Card } from '@/components/ui/card'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import type { Webhook } from '../api'
import { useDeleteWebhook, useTestWebhook, useWebhooks } from '../hooks'
import { DeliveriesDialog } from './DeliveriesDialog'
import { WebhookEditorDialog } from './WebhookEditorDialog'

async function copy(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  } catch {
    toast.error('Could not copy')
  }
}

export function WebhooksTab() {
  const canManage = useHasPerm('webhooks:manage')
  const webhooks = useWebhooks()
  const test = useTestWebhook()
  const remove = useDeleteWebhook()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Webhook | null>(null)
  const [deliveriesFor, setDeliveriesFor] = useState<Webhook | null>(null)
  const [deleting, setDeleting] = useState<Webhook | null>(null)
  const [revealed, setRevealed] = useState<Record<string, boolean>>({})

  if (!canManage) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        You need the “Manage webhooks” permission to view outbound webhooks.
      </Card>
    )
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Send domain events to external URLs.</p>
        <Button
          size="sm"
          onClick={() => {
            setEditing(null)
            setEditorOpen(true)
          }}
        >
          <Plus className="size-4" /> New webhook
        </Button>
      </div>

      {webhooks.isLoading ? (
        <div className="grid gap-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : webhooks.isError ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load webhooks.{' '}
          <Button variant="link" className="px-1" onClick={() => webhooks.refetch()}>
            Retry
          </Button>
        </Card>
      ) : !webhooks.data || webhooks.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <WebhookIcon />
            </EmptyMedia>
            <EmptyTitle>No webhooks yet</EmptyTitle>
            <EmptyDescription>
              Notify external systems whenever things happen in your workspace.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button
              onClick={() => {
                setEditing(null)
                setEditorOpen(true)
              }}
            >
              <Plus className="size-4" /> Add a webhook
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {webhooks.data.map((webhook) => (
            <li key={webhook.id}>
              <Card className="grid gap-3 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-mono text-sm">{webhook.url}</span>
                      {webhook.enabled ? (
                        <Badge variant="secondary">Enabled</Badge>
                      ) : (
                        <Badge variant="outline">Disabled</Badge>
                      )}
                    </div>
                    {webhook.description ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">{webhook.description}</p>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-1">
                      {webhook.events.map((event) => (
                        <Badge key={event} variant="outline" className="font-mono text-[10px]">
                          {event}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={test.isPending}
                      onClick={() => test.mutate(webhook.id)}
                    >
                      <Send className="size-4" /> Test
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setDeliveriesFor(webhook)}>
                      Deliveries
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Edit webhook"
                      onClick={() => {
                        setEditing(webhook)
                        setEditorOpen(true)
                      }}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Delete webhook"
                      onClick={() => setDeleting(webhook)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>

                <div className="flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs">
                  <span className="text-muted-foreground">Signing secret</span>
                  <code className="flex-1 truncate font-mono">
                    {revealed[webhook.id] ? webhook.secret : '•'.repeat(24)}
                  </code>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={revealed[webhook.id] ? 'Hide secret' : 'Reveal secret'}
                    onClick={() =>
                      setRevealed((prev) => ({ ...prev, [webhook.id]: !prev[webhook.id] }))
                    }
                  >
                    {revealed[webhook.id] ? (
                      <EyeOff className="size-4" />
                    ) : (
                      <Eye className="size-4" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label="Copy secret"
                    onClick={() => copy(webhook.secret, 'Secret')}
                  >
                    <Copy className="size-4" />
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <WebhookEditorDialog open={editorOpen} onOpenChange={setEditorOpen} webhook={editing} />
      <DeliveriesDialog
        webhook={deliveriesFor}
        onOpenChange={(open) => !open && setDeliveriesFor(null)}
      />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this webhook?</AlertDialogTitle>
            <AlertDialogDescription>
              Deliveries will stop immediately. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id)
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
