/**
 * Watchers on a conversation.
 *
 * Participation is mostly implicit — assignees and note authors are added
 * automatically — so this card is mainly for joining a thread you care about
 * and leaving one you don't. Leaving is recorded as a mute, which is why a
 * later re-assignment doesn't quietly resubscribe you.
 */

import { Eye, EyeOff, UserPlus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAuthStore } from '@/stores/auth'
import { useMembers, useParticipants, useToggleParticipant } from '@/features/inbox/hooks'

export function WatchersCard({ conversationId }: { conversationId: string }) {
  const userId = useAuthStore((s) => s.user?.id)
  const { data: participants = [] } = useParticipants(conversationId)
  const { data: members = [] } = useMembers()
  const toggle = useToggleParticipant(conversationId)

  const active = participants.filter((p) => !p.muted)
  const byUser = new Map(members.map((m) => [m.user.id, m.user.name]))
  const watching = userId ? active.some((p) => p.user_id === userId) : false
  const candidates = members.filter((m) => !active.some((p) => p.user_id === m.user.id))

  return (
    <section className="space-y-2 border-b p-3">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold uppercase text-muted-foreground">Watchers</h3>
        {userId ? (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 px-1.5 text-xs"
            onClick={() => toggle.mutate({ userId, join: !watching })}
            disabled={toggle.isPending}
          >
            {watching ? (
              <>
                <EyeOff className="mr-1 size-3.5" />
                Leave
              </>
            ) : (
              <>
                <Eye className="mr-1 size-3.5" />
                Watch
              </>
            )}
          </Button>
        ) : null}
      </div>

      {active.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nobody is watching this thread.</p>
      ) : (
        <ul className="space-y-1">
          {active.map((participant) => (
            <li
              key={participant.id}
              className="flex items-center gap-2 text-sm"
              title={`Added because: ${participant.reason}`}
            >
              <span className="truncate">
                {byUser.get(participant.user_id) ?? 'Unknown member'}
              </span>
              <span className="ml-auto text-[10px] uppercase text-muted-foreground">
                {participant.reason}
              </span>
            </li>
          ))}
        </ul>
      )}

      {candidates.length > 0 ? (
        <Select onValueChange={(id) => toggle.mutate({ userId: id, join: true })}>
          <SelectTrigger className="h-7 w-full text-xs" aria-label="Add watcher">
            <UserPlus className="mr-1 size-3.5" />
            <SelectValue placeholder="Add someone" />
          </SelectTrigger>
          <SelectContent>
            {candidates.map((member) => (
              <SelectItem key={member.user.id} value={member.user.id}>
                {member.user.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
    </section>
  )
}
