/** A single row in the conversation list. */

import { cn } from '@/lib/utils'
import { timeAgo } from '@/lib/format'
import { ChannelIcon, ContactAvatar, PriorityFlag } from '@/features/inbox/components/atoms'
import type { ConversationListItem, Tag } from '@/features/inbox/api'
import { t } from '@/i18n'

export function ConversationRow({
  item,
  active,
  assigneeOnline,
  tagsById,
  onSelect,
  selectable = false,
  selected = false,
  onToggleSelected,
}: {
  item: ConversationListItem
  active: boolean
  assigneeOnline?: boolean
  tagsById: Map<string, Tag>
  onSelect: (id: string) => void
  selectable?: boolean
  selected?: boolean
  onToggleSelected?: (id: string) => void
}) {
  return (
    <div
      className={cn(
        'flex w-full items-start border-b transition-colors hover:bg-accent/50',
        active && 'bg-accent',
        selected && 'bg-brand/10'
      )}
    >
      {selectable ? (
        // Outside the row button: a checkbox nested in a `<button>` is invalid
        // markup and swallows the click.
        <label className="flex cursor-pointer items-start px-2 pt-4">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={selected}
            onChange={() => onToggleSelected?.(item.id)}
            aria-label={`Select conversation with ${item.contact.name || 'unknown contact'}`}
          />
        </label>
      ) : null}
      <button
        type="button"
        onClick={() => onSelect(item.id)}
        aria-current={active ? 'true' : undefined}
        className={cn('flex min-w-0 flex-1 gap-3 px-3 py-3 text-left', selectable && 'pl-1')}
      >
        <ContactAvatar
          name={item.contact.name}
          avatarUrl={item.contact.avatar_url}
          online={assigneeOnline}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn('truncate text-sm', item.unread ? 'font-semibold' : 'font-medium')}>
              {item.contact.name || 'Unknown'}
            </span>
            {item.status === 'pending' ? (
              <span className="rounded bg-brand-soft px-1 text-[10px] font-semibold text-brand">
                AI
              </span>
            ) : null}
            <span className="ml-auto flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
              <ChannelIcon channel={item.inbox.channel_type} />
              {timeAgo(item.last_activity_at)}
            </span>
          </div>

          {item.subject ? (
            <p className="truncate text-xs font-medium text-foreground/70">{item.subject}</p>
          ) : null}
          <p
            className={cn(
              'truncate text-xs',
              item.unread ? 'text-foreground' : 'text-muted-foreground'
            )}
          >
            {item.last_message_preview ?? 'No messages yet'}
          </p>

          <div className="mt-1 flex items-center gap-2">
            {item.unread ? (
              <span className="size-2 rounded-full bg-brand" aria-label={t('inbox.unread')} />
            ) : null}
            <PriorityFlag priority={item.priority} />
            {item.waiting_since ? (
              <span className="text-[11px] text-amber-600 dark:text-amber-400">
                waiting {timeAgo(item.waiting_since)}
              </span>
            ) : null}
            <span className="ml-auto flex items-center gap-1">
              {(item.tag_ids ?? []).slice(0, 3).map((id) => {
                const tag = tagsById.get(id)
                if (!tag) return null
                return (
                  <span
                    key={id}
                    className="size-2 rounded-full"
                    style={{ backgroundColor: tag.color }}
                    title={tag.name}
                  />
                )
              })}
            </span>
          </div>
        </div>
      </button>
    </div>
  )
}
