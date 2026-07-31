/** Scrollable message timeline: day separators, load-older, typing indicator. */

import { useEffect, useRef } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { MessageBubble } from '@/features/inbox/components/MessageBubble'
import type { Message } from '@/features/inbox/api'

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  if (sameDay(d, today)) return 'Today'
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (sameDay(d, yesterday)) return 'Yesterday'
  return d.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}),
  })
}

export function MessageTimeline({
  messages,
  isLoading,
  isError,
  onRetry,
  hasOlder,
  loadingOlder,
  onLoadOlder,
  typing,
  typingLabel,
}: {
  messages: Message[]
  isLoading: boolean
  isError: boolean
  onRetry: () => void
  hasOlder: boolean
  loadingOlder: boolean
  onLoadOlder: () => void
  typing: boolean
  typingLabel: string
}) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const lastId = messages.at(-1)?.id

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [lastId, typing])

  if (isLoading) {
    return (
      <div className="flex-1 space-y-4 overflow-y-auto p-4" data-testid="thread-loading">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1">
            <Skeleton className="h-3 w-24" />
            <Skeleton className={`h-12 ${i % 2 ? 'w-1/2 self-end' : 'w-2/3'} rounded-2xl`} />
          </div>
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <AlertCircle className="size-8 text-destructive" />
        <p className="text-sm text-muted-foreground">Could not load this conversation.</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    )
  }

  let lastDay = ''
  return (
    <div className="flex-1 overflow-y-auto p-4" data-testid="message-timeline">
      {hasOlder ? (
        <div className="mb-3 flex justify-center">
          <Button variant="ghost" size="sm" onClick={onLoadOlder} disabled={loadingOlder}>
            {loadingOlder ? <Loader2 className="size-3.5 animate-spin" /> : null}
            Load older messages
          </Button>
        </div>
      ) : null}

      <div className="flex flex-col gap-4">
        {messages.map((message) => {
          const day = dayLabel(message.created_at)
          const showDay = day !== lastDay
          lastDay = day
          return (
            <div key={message.id} className="flex flex-col gap-4">
              {showDay ? (
                <div className="flex items-center justify-center">
                  <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                    {day}
                  </span>
                </div>
              ) : null}
              <MessageBubble message={message} />
            </div>
          )
        })}
      </div>

      {typing ? (
        <div className="mt-3 flex items-center gap-2 px-1 text-xs text-muted-foreground" data-testid="typing-indicator">
          <span className="flex gap-0.5">
            <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.2s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.1s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground" />
          </span>
          {typingLabel} is typing…
        </div>
      ) : null}

      <div ref={bottomRef} />
    </div>
  )
}
