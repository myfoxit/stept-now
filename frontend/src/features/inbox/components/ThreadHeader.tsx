/** Thread pane header: identity + status/priority/assignee/team/snooze controls. */

import { Check, ChevronDown, Clock, Copy, UserRound } from 'lucide-react'
import { toast } from 'sonner'

import { useAuthStore, useHasPerm } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ChannelIcon, StatusBadge } from '@/features/inbox/components/atoms'
import { usePatchConversation, useMembers, useTeams } from '@/features/inbox/hooks'
import type { Conversation, Priority } from '@/features/inbox/api'

const SNOOZE_PRESETS: { label: string; ms: number }[] = [
  { label: 'In 1 hour', ms: 60 * 60 * 1000 },
  { label: 'In 3 hours', ms: 3 * 60 * 60 * 1000 },
  { label: 'Tomorrow', ms: 24 * 60 * 60 * 1000 },
  { label: 'Next week', ms: 7 * 24 * 60 * 60 * 1000 },
]

export function ThreadHeader({ conversation }: { conversation: Conversation }) {
  const canManage = useHasPerm('conversations:manage')
  const myId = useAuthStore((s) => s.user?.id)
  const patch = usePatchConversation(conversation.id)
  const { data: members = [] } = useMembers()
  const { data: teams = [] } = useTeams()

  function copyNumber() {
    navigator.clipboard?.writeText(`#${conversation.number}`).then(
      () => toast.success(`Copied #${conversation.number}`),
      () => undefined
    )
  }

  const assigneeName = conversation.assignee?.name ?? 'Unassigned'
  const teamName = teams.find((t) => t.id === conversation.team_id)?.name

  return (
    <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="truncate text-sm font-semibold">
            {conversation.contact.name || 'Unknown contact'}
          </h2>
          <StatusBadge status={conversation.status} />
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <ChannelIcon channel={conversation.inbox.channel_type} />
          <span>{conversation.inbox.name}</span>
          <button
            type="button"
            onClick={copyNumber}
            className="inline-flex items-center gap-1 hover:text-foreground"
            aria-label="Copy conversation number"
          >
            #{conversation.number}
            <Copy className="size-3" />
          </button>
        </div>
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        {/* Priority */}
        <Select
          value={conversation.priority}
          onValueChange={(v) => patch.mutate({ priority: v as Priority })}
          disabled={!canManage}
        >
          <SelectTrigger size="sm" aria-label="Priority" className="w-[7.5rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(['none', 'low', 'medium', 'high', 'urgent'] as Priority[]).map((p) => (
              <SelectItem key={p} value={p} className="capitalize">
                {p === 'none' ? 'No priority' : p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Assignee */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild disabled={!canManage}>
            <Button variant="outline" size="sm">
              <UserRound className="size-4" />
              <span className="max-w-[8rem] truncate">{assigneeName}</span>
              <ChevronDown className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuLabel>Assign to</DropdownMenuLabel>
            <DropdownMenuItem onClick={() => patch.mutate({ assignee_user_id: null })}>
              Unassigned
              {!conversation.assignee ? <Check className="ml-auto size-4" /> : null}
            </DropdownMenuItem>
            {myId ? (
              <DropdownMenuItem onClick={() => patch.mutate({ assignee_user_id: myId })}>
                Assign to me
                {conversation.assignee?.id === myId ? <Check className="ml-auto size-4" /> : null}
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            {members.map((m) => (
              <DropdownMenuItem
                key={m.id}
                onClick={() => patch.mutate({ assignee_user_id: m.user.id })}
              >
                <span className="truncate">{m.user.name}</span>
                {conversation.assignee?.id === m.user.id ? (
                  <Check className="ml-auto size-4" />
                ) : null}
              </DropdownMenuItem>
            ))}
            {teams.length ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Team</DropdownMenuLabel>
                <DropdownMenuItem onClick={() => patch.mutate({ team_id: null })}>
                  No team
                  {!conversation.team_id ? <Check className="ml-auto size-4" /> : null}
                </DropdownMenuItem>
                {teams.map((t) => (
                  <DropdownMenuItem key={t.id} onClick={() => patch.mutate({ team_id: t.id })}>
                    <span className="truncate">
                      {t.icon ? `${t.icon} ` : ''}
                      {t.name}
                    </span>
                    {conversation.team_id === t.id ? <Check className="ml-auto size-4" /> : null}
                  </DropdownMenuItem>
                ))}
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>

        {teamName ? (
          <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">{teamName}</span>
        ) : null}

        {/* Snooze */}
        <Popover>
          <PopoverTrigger asChild disabled={!canManage}>
            <Button variant="outline" size="icon-sm" aria-label="Snooze">
              <Clock className="size-4" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-52 space-y-1">
            <p className="px-1 pb-1 text-xs font-medium text-muted-foreground">Snooze until</p>
            {SNOOZE_PRESETS.map((preset) => (
              <Button
                key={preset.label}
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                onClick={() =>
                  patch.mutate({
                    status: 'snoozed',
                    snoozed_until: new Date(Date.now() + preset.ms).toISOString(),
                  })
                }
              >
                {preset.label}
              </Button>
            ))}
            <label className="block px-1 pt-1 text-xs text-muted-foreground">Custom</label>
            <input
              type="datetime-local"
              aria-label="Custom snooze time"
              className="w-full rounded-md border bg-transparent px-2 py-1 text-sm"
              onChange={(e) => {
                if (!e.target.value) return
                patch.mutate({
                  status: 'snoozed',
                  snoozed_until: new Date(e.target.value).toISOString(),
                })
              }}
            />
          </PopoverContent>
        </Popover>

        {/* Resolve / Reopen */}
        {conversation.status === 'resolved' ? (
          <Button
            variant="outline"
            size="sm"
            disabled={!canManage}
            onClick={() => patch.mutate({ status: 'open' })}
          >
            Reopen
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={!canManage}
            onClick={() => patch.mutate({ status: 'resolved' })}
          >
            <Check className="size-4" />
            Resolve
          </Button>
        )}
      </div>
    </div>
  )
}
