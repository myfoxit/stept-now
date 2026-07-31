/** Inbox — the flagship 3-pane screen (list · thread · context). */

import { useNavigate, useParams } from 'react-router'

import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { ConversationListPane } from '@/features/inbox/components/ConversationListPane'
import { ContextPane } from '@/features/inbox/components/ContextPane'
import { ThreadPane } from '@/features/inbox/components/ThreadPane'
import { useConversation, useInboxRealtime } from '@/features/inbox/hooks'

export function Component() {
  const { conversationId } = useParams()
  const navigate = useNavigate()

  useInboxRealtime(conversationId)
  const conversationQuery = useConversation(conversationId)

  return (
    <div className="h-full" data-tour="inbox">
      <ResizablePanelGroup orientation="horizontal" className="h-full">
        <ResizablePanel defaultSize="26" minSize="20" className="min-w-[16rem]">
          <ConversationListPane
            activeId={conversationId}
            onSelect={(id) => navigate(`/inbox/${id}`)}
          />
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel defaultSize="48" minSize="30">
          <ThreadPane conversationId={conversationId} />
        </ResizablePanel>

        {conversationId ? (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize="26" minSize="18" className="hidden lg:block">
              {conversationQuery.data ? <ContextPane conversation={conversationQuery.data} /> : null}
            </ResizablePanel>
          </>
        ) : null}
      </ResizablePanelGroup>
    </div>
  )
}

export default Component
