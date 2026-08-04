import { useEffect, useRef } from 'preact/hooks'

import type { FeedbackRating } from '../../types'
import type { UiMessage } from '../controller'
import { Composer } from './Composer'
import { Csat } from './Csat'
import { MessageBubble } from './MessageBubble'
import { PageAssist } from './PageAssist'
import { TypingDots } from './TypingDots'

export function Thread({
  messages,
  agentTyping,
  hasMore,
  loading,
  status,
  csatDone,
  greeting,
  widgetKey,
  pageControl,
  pageTitle,
  actionsAllowed,
  workingOnPage,
  onSend,
  onTyping,
  onLoadMore,
  onCsat,
  onFeedback,
  onAllowActions,
}: {
  messages: UiMessage[]
  agentTyping: boolean
  hasMore: boolean
  loading: boolean
  status: string | null
  csatDone: boolean
  greeting: string
  widgetKey: string
  /** The workspace's agent can see this page — show the assist row. */
  pageControl: boolean
  pageTitle: string
  actionsAllowed: boolean
  workingOnPage: string | null
  onSend: (text: string) => void
  onTyping: (isTyping: boolean) => void
  onLoadMore: () => void
  onCsat: (rating: number, feedback?: string) => void
  onFeedback: (messageId: string, rating: FeedbackRating) => void
  onAllowActions: (allowed: boolean) => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)

  useEffect(() => {
    if (atBottom.current && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight
    }
  }, [messages, agentTyping])

  const onScroll = (): void => {
    const el = scroller.current
    if (!el) return
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const resolved = status === 'resolved'

  return (
    <div class="sw-thread">
      <div class="sw-messages" ref={scroller} onScroll={onScroll}>
        {hasMore && (
          <button type="button" class="sw-loadmore" onClick={onLoadMore}>
            Load earlier messages
          </button>
        )}
        {loading && messages.length === 0 && <div class="sw-loading">Loading…</div>}
        {!loading && messages.length === 0 && (
          <div class="sw-thread-intro">
            <p>{greeting}</p>
            <p class="sw-muted">Send a message and we&rsquo;ll get back to you here.</p>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} widgetKey={widgetKey} onFeedback={onFeedback} />
        ))}
        {agentTyping && <TypingDots />}
        {resolved && (
          <Csat done={csatDone} onSubmit={onCsat} />
        )}
      </div>
      {pageControl && !resolved && (
        <PageAssist
          pageTitle={pageTitle}
          actionsAllowed={actionsAllowed}
          working={workingOnPage}
          onAllowChange={onAllowActions}
        />
      )}
      <Composer onSend={onSend} onTyping={onTyping} />
    </div>
  )
}
