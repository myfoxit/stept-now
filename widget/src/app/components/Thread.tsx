import { useEffect, useRef } from 'preact/hooks'

import type { UiMessage } from '../controller'
import { Composer } from './Composer'
import { Csat } from './Csat'
import { MessageBubble } from './MessageBubble'
import { TypingDots } from './TypingDots'

export function Thread({
  messages,
  agentTyping,
  hasMore,
  loading,
  status,
  csatDone,
  greeting,
  onSend,
  onTyping,
  onLoadMore,
  onCsat,
}: {
  messages: UiMessage[]
  agentTyping: boolean
  hasMore: boolean
  loading: boolean
  status: string | null
  csatDone: boolean
  greeting: string
  onSend: (text: string) => void
  onTyping: (isTyping: boolean) => void
  onLoadMore: () => void
  onCsat: (rating: number, feedback?: string) => void
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
          <MessageBubble key={m.id} message={m} />
        ))}
        {agentTyping && <TypingDots />}
        {resolved && (
          <Csat done={csatDone} onSubmit={onCsat} />
        )}
      </div>
      <Composer onSend={onSend} onTyping={onTyping} />
    </div>
  )
}
