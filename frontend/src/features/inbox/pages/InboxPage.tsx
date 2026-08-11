/** Inbox — the flagship 3-pane screen (list · thread · context). */

import { useNavigate, useParams } from 'react-router'
import { ChevronLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useIsMobile } from '@/hooks/use-mobile'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { ConversationListPane } from '@/features/inbox/components/ConversationListPane'
import { ContextPane } from '@/features/inbox/components/ContextPane'
import { ThreadPane } from '@/features/inbox/components/ThreadPane'
import { useConversation, useInboxRealtime } from '@/features/inbox/hooks'
import { t } from '@/i18n'

export function Component() {
  const { conversationId } = useParams()
  const navigate = useNavigate()

  useInboxRealtime(conversationId)
  const conversationQuery = useConversation(conversationId)
  const isMobile = useIsMobile()

  // Master/detail on a phone. The resizable 3-pane group has ~46rem of minimums,
  // so on a 390px screen every pane was squeezed to unusable width at once.
  if (isMobile) {
    return (
      <div className="flex h-full flex-col" data-tour="inbox">
        {conversationId ? (
          <>
            <div className="border-b px-2 py-1.5">
              <Button variant="ghost" size="sm" onClick={() => navigate('/inbox')}>
                <ChevronLeft className="size-4" /> {t('inbox.all_conversations')}
              </Button>
            </div>
            <div className="min-h-0 flex-1">
              <ThreadPane conversationId={conversationId} />
            </div>
          </>
        ) : (
          <ConversationListPane
            activeId={conversationId}
            onSelect={(id) => navigate(`/inbox/${id}`)}
          />
        )}
      </div>
    )
  }

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
