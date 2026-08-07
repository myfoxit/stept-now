/**
 * Bulk action bar — appears once rows are selected.
 *
 * Every action posts to `/conversations/bulk`, which loops through the same
 * service functions a single edit uses, so trackers, activity notes and
 * realtime updates all still fire.
 */

import { X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { BulkAction, Member, Tag, Team } from '@/features/inbox/api'
import { useBulkAction } from '@/features/inbox/hooks'

const STATUSES = ['open', 'pending', 'resolved'] as const
const PRIORITIES = ['urgent', 'high', 'medium', 'low', 'none'] as const

export function BulkActionBar({
  selected,
  members,
  teams,
  tags,
  onDone,
}: {
  selected: string[]
  members: Member[]
  teams: Team[]
  tags: Tag[]
  onDone: () => void
}) {
  const bulk = useBulkAction()

  function run(action: BulkAction, params: Record<string, unknown>) {
    bulk.mutate({ action, params, conversation_ids: selected }, { onSuccess: onDone })
  }

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b bg-accent/50 px-2 py-1.5"
      role="toolbar"
      aria-label="Bulk actions"
    >
      <span className="text-xs font-medium">{selected.length} selected</span>

      <Select onValueChange={(status) => run('set_status', { status })}>
        <SelectTrigger className="h-7 w-28 text-xs" aria-label="Set status">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          {STATUSES.map((status) => (
            <SelectItem key={status} value={status} className="capitalize">
              {status}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select onValueChange={(priority) => run('set_priority', { priority })}>
        <SelectTrigger className="h-7 w-28 text-xs" aria-label="Set priority">
          <SelectValue placeholder="Priority" />
        </SelectTrigger>
        <SelectContent>
          {PRIORITIES.map((priority) => (
            <SelectItem key={priority} value={priority} className="capitalize">
              {priority}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        onValueChange={(value) =>
          run('assign_user', { assignee_user_id: value === '__none__' ? null : value })
        }
      >
        <SelectTrigger className="h-7 w-32 text-xs" aria-label="Assign to">
          <SelectValue placeholder="Assign" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="self">Me</SelectItem>
          <SelectItem value="__none__">Unassign</SelectItem>
          {members.map((member) => (
            <SelectItem key={member.user.id} value={member.user.id}>
              {member.user.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {teams.length > 0 ? (
        <Select
          onValueChange={(value) =>
            run('assign_team', { team_id: value === '__none__' ? null : value })
          }
        >
          <SelectTrigger className="h-7 w-28 text-xs" aria-label="Assign team">
            <SelectValue placeholder="Team" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">No team</SelectItem>
            {teams.map((team) => (
              <SelectItem key={team.id} value={team.id}>
                {team.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}

      {tags.length > 0 ? (
        <Select onValueChange={(tagId) => run('add_tag', { tag_id: tagId })}>
          <SelectTrigger className="h-7 w-28 text-xs" aria-label="Add tag">
            <SelectValue placeholder="Add tag" />
          </SelectTrigger>
          <SelectContent>
            {tags.map((tag) => (
              <SelectItem key={tag.id} value={tag.id}>
                {tag.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}

      <Button
        variant="ghost"
        size="icon"
        className="ml-auto size-7"
        aria-label="Clear selection"
        onClick={onDone}
      >
        <X className="size-4" />
      </Button>
    </div>
  )
}
