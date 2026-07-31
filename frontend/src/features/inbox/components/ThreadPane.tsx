/** Center pane: header + message timeline + composer for the open conversation. */

import { useEffect } from 'react'
import { MessagesSquare } from 'lucide-react'

import { useHasPerm } from '@/stores/auth'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Composer } from '@/features/inbox/components/Composer'
import { MessageTimeline } from '@/features/inbox/components/MessageTimeline'
import { ThreadHeader } from '@/features/inbox/components/ThreadHeader'
import {
  flattenMessages,
  useConversation,
  useMarkRead,
  useMessages,
  useTypingIndicator,
} from '@/features/inbox/hooks'

export function ThreadPane({ conversationId }: { conversationId?: string }) {
  const canWrite = useHasPerm('conversations:write')
  const conversationQuery = useConversation(conversationId)
  const messagesQuery = useMessages(conversationId)
  const typing = useTypingIndicator(conversationId)
  const markRead = useMarkRead(conversationId)

  useEffect(() => {
    if (conversationId && canWrite) markRead.mutate()
    // markRead.mutate is stable across renders (react-query)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, canWrite])

  if (!conversationId) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <MessagesSquare className="size-5" />
            </EmptyMedia>
            <EmptyTitle>No conversation selected</EmptyTitle>
            <EmptyDescription>Pick a conversation from the list to get started.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  const messages = flattenMessages(messagesQuery.data)

  return (
    <div className="flex h-full flex-col">
      {conversationQuery.data ? <ThreadHeader conversation={conversationQuery.data} /> : null}

      <MessageTimeline
        messages={messages}
        isLoading={messagesQuery.isLoading}
        isError={messagesQuery.isError}
        onRetry={() => messagesQuery.refetch()}
        hasOlder={!!messagesQuery.hasNextPage}
        loadingOlder={messagesQuery.isFetchingNextPage}
        onLoadOlder={() => messagesQuery.fetchNextPage()}
        typing={typing}
        typingLabel={conversationQuery.data?.contact.name ?? 'Someone'}
      />

      <Composer
        conversationId={conversationId}
        contactName={conversationQuery.data?.contact.name ?? ''}
        canWrite={canWrite}
      />
    </div>
  )
}
