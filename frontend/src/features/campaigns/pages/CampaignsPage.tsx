import { CalendarClock, Megaphone, Pause, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

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
import { Switch } from '@/components/ui/switch'
import { useHasPerm } from '@/stores/auth'

import type { Campaign, Inbox } from '../api'
import { CampaignEditorDialog } from '../components/CampaignEditorDialog'
import { CampaignStatusBadge } from '../components/CampaignStatusBadge'
import {
  useActivateCampaign,
  useCampaignInboxes,
  useCampaigns,
  useDeleteCampaign,
  usePauseCampaign,
  useUpdateCampaign,
} from '../hooks'
import { describeTrigger } from '../lib'

function CampaignTypeBadge({ type }: { type: string }) {
  if (type === 'ongoing') {
    return (
      <Badge variant="outline">
        <Megaphone className="size-3" /> In-app
      </Badge>
    )
  }
  return (
    <Badge variant="outline">
      <CalendarClock className="size-3" /> Scheduled
    </Badge>
  )
}

function CampaignRow({
  campaign,
  inbox,
  canManage,
  onEdit,
  onDelete,
}: {
  campaign: Campaign
  inbox: Inbox | null
  canManage: boolean
  onEdit: (campaign: Campaign) => void
  onDelete: (campaign: Campaign) => void
}) {
  const update = useUpdateCampaign()
  const activate = useActivateCampaign()
  const pause = usePauseCampaign()
  const processing = campaign.status === 'processing'

  return (
    <Card className="flex flex-row items-center gap-4 p-4">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{campaign.title}</span>
          <CampaignTypeBadge type={campaign.campaign_type} />
          <CampaignStatusBadge status={campaign.status} />
        </div>
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {inbox ? `${inbox.name} · ${inbox.channel_type} · ` : ''}
          {describeTrigger(campaign)}
          {' · '}
          {campaign.sent_count} sent
        </p>
      </div>

      <Switch
        checked={campaign.enabled}
        disabled={!canManage || processing || update.isPending}
        aria-label={`Enable ${campaign.title}`}
        onCheckedChange={(checked) =>
          update.mutate({ id: campaign.id, body: { enabled: checked } })
        }
      />

      {canManage ? (
        <>
          {campaign.status === 'draft' ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Activate ${campaign.title}`}
              disabled={activate.isPending}
              onClick={() => activate.mutate(campaign.id)}
            >
              <Play className="size-4" />
            </Button>
          ) : campaign.status === 'active' ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Pause ${campaign.title}`}
              disabled={pause.isPending}
              onClick={() => pause.mutate(campaign.id)}
            >
              <Pause className="size-4" />
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Edit ${campaign.title}`}
            disabled={processing}
            onClick={() => onEdit(campaign)}
          >
            <Pencil className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete ${campaign.title}`}
            onClick={() => onDelete(campaign)}
          >
            <Trash2 className="size-4" />
          </Button>
        </>
      ) : null}
    </Card>
  )
}

export function Component() {
  const canRead = useHasPerm('automations:read')
  const canManage = useHasPerm('automations:manage')
  const campaigns = useCampaigns(canRead)
  const inboxes = useCampaignInboxes(canRead)
  const remove = useDeleteCampaign()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Campaign | null>(null)
  const [deleting, setDeleting] = useState<Campaign | null>(null)

  function openNew() {
    setEditing(null)
    setEditorOpen(true)
  }
  function openEdit(campaign: Campaign) {
    setEditing(campaign)
    setEditorOpen(true)
  }

  const inboxById = new Map((inboxes.data ?? []).map((inbox) => [inbox.id, inbox]))

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">Campaigns</h1>
          <p className="text-sm text-muted-foreground">
            Proactive messages to your visitors and customers.
          </p>
        </div>
        {canManage ? (
          <Button onClick={openNew}>
            <Plus className="size-4" /> New campaign
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          {!canRead ? (
            <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
              You don&rsquo;t have access to campaigns.
            </p>
          ) : campaigns.isLoading ? (
            <div className="grid gap-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : campaigns.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load campaigns.{' '}
              <Button variant="link" className="px-1" onClick={() => campaigns.refetch()}>
                Retry
              </Button>
            </Card>
          ) : !campaigns.data || campaigns.data.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Megaphone />
                </EmptyMedia>
                <EmptyTitle>No campaigns yet</EmptyTitle>
                <EmptyDescription>
                  Announce features in the widget, or schedule a send to an audience.
                </EmptyDescription>
              </EmptyHeader>
              {canManage ? (
                <EmptyContent>
                  <Button onClick={openNew}>
                    <Plus className="size-4" /> Create your first campaign
                  </Button>
                </EmptyContent>
              ) : null}
            </Empty>
          ) : (
            <ul className="grid gap-3">
              {campaigns.data.map((campaign) => (
                <li key={campaign.id}>
                  <CampaignRow
                    campaign={campaign}
                    inbox={inboxById.get(campaign.inbox_id) ?? null}
                    canManage={canManage}
                    onEdit={openEdit}
                    onDelete={setDeleting}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <CampaignEditorDialog open={editorOpen} onOpenChange={setEditorOpen} campaign={editing} />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{deleting?.title}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              The campaign stops sending immediately. This action cannot be undone.
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

export default Component
