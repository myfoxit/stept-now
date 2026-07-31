/** Right pane: contact card, conversation tags, approvals, recent conversations. */

import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { BadgeCheck, ExternalLink } from 'lucide-react'

import { useAuthStore, useHasPerm } from '@/stores/auth'
import { Separator } from '@/components/ui/separator'
import { timeAgo } from '@/lib/format'
import { ContactAvatar, PriorityFlag, StatusBadge } from '@/features/inbox/components/atoms'
import { ApprovalCard } from '@/features/inbox/components/ApprovalCard'
import { SlaCard } from '@/features/inbox/components/SlaCard'
import { TagsEditor } from '@/features/inbox/components/TagsEditor'
import { inboxApi, type Conversation } from '@/features/inbox/api'
import { useConversationTags, usePendingApprovals } from '@/features/inbox/hooks'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </div>
  )
}

export function ContextPane({ conversation }: { conversation: Conversation }) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const canManage = useHasPerm('conversations:manage')
  const canApprove = useHasPerm('ai:approve')
  const tags = useConversationTags(conversation.id)
  const { data: approvals = [] } = usePendingApprovals()
  const contact = conversation.contact

  const { data: recent } = useQuery({
    queryKey: ['inbox', workspaceId, 'contact-conversations', contact.id],
    enabled: !!workspaceId,
    queryFn: () => inboxApi.listConversations({ contact_id: contact.id }),
  })

  const pending = approvals.filter((a) => a.conversation_id === conversation.id)
  const otherConversations = (recent?.items ?? []).filter((c) => c.id !== conversation.id)
  const attributes = Object.entries(contact.attributes ?? {})

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="space-y-5 p-4">
        {/* Contact card */}
        <div className="flex flex-col items-center gap-2 text-center">
          <ContactAvatar name={contact.name} avatarUrl={contact.avatar_url} size="lg" />
          <div>
            <div className="flex items-center justify-center gap-1">
              <span className="font-semibold">{contact.name || 'Unknown contact'}</span>
              {contact.verified ? <BadgeCheck className="size-4 text-blue-500" /> : null}
            </div>
            {contact.email ? (
              <p className="text-sm text-muted-foreground">{contact.email}</p>
            ) : null}
          </div>
          <Link
            to={`/contacts/${contact.id}`}
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            View full profile <ExternalLink className="size-3" />
          </Link>
        </div>

        <Separator />

        <Section title="Tags">
          <TagsEditor
            appliedTagIds={conversation.tag_ids ?? []}
            canManage={canManage}
            onAdd={(id) => tags.add.mutate(id)}
            onRemove={(id) => tags.remove.mutate(id)}
          />
        </Section>

        <Section title="SLA">
          <SlaCard conversationId={conversation.id} />
        </Section>

        {pending.length ? (
          <Section title="Pending approvals">
            <div className="space-y-2">
              {pending.map((a) => (
                <ApprovalCard key={a.id} approval={a} canApprove={canApprove} />
              ))}
            </div>
          </Section>
        ) : null}

        {attributes.length ? (
          <Section title="Attributes">
            <dl className="space-y-1 text-sm">
              {attributes.map(([key, value]) => (
                <div key={key} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{key}</dt>
                  <dd className="max-w-[60%] truncate text-right font-medium">{String(value)}</dd>
                </div>
              ))}
            </dl>
          </Section>
        ) : null}

        <Section title="Recent conversations">
          {otherConversations.length ? (
            <ul className="space-y-1">
              {otherConversations.slice(0, 6).map((c) => (
                <li key={c.id}>
                  <Link
                    to={`/inbox/${c.id}`}
                    className="block rounded-md border px-2.5 py-2 text-sm hover:bg-accent"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <StatusBadge status={c.status} />
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <PriorityFlag priority={c.priority} />
                        {timeAgo(c.last_activity_at)}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {c.subject || c.last_message_preview || `#${c.number}`}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">No other conversations.</p>
          )}
        </Section>
      </div>
    </div>
  )
}
