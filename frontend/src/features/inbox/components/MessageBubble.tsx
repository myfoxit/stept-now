/** Renders a single message: public bubble, private note, or activity line. */

import { AlertCircle, Bot, FileText, Loader2, Lock, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import { formatBytes, initials, messageTime } from '@/lib/format'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { useHasPerm } from '@/stores/auth'
import { fileUrl, messageCitations, type Message } from '@/features/inbox/api'
import { useMessageFeedback, useSubmitMessageFeedback } from '@/features/inbox/hooks'

interface Attachment {
  key: string
  name: string
  size?: number
  content_type?: string
}

function Attachments({ attachments }: { attachments: Attachment[] }) {
  if (!attachments.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {attachments.map((a) => {
        const isImage = (a.content_type ?? '').startsWith('image/')
        if (isImage) {
          return (
            <a
              key={a.key}
              href={fileUrl(a.key)}
              target="_blank"
              rel="noreferrer"
              className="block overflow-hidden rounded-lg border"
            >
              <img src={fileUrl(a.key)} alt={a.name} className="max-h-48 max-w-full object-cover" />
            </a>
          )
        }
        return (
          <a
            key={a.key}
            href={fileUrl(a.key)}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 rounded-lg border bg-background/60 px-2.5 py-1.5 text-xs hover:bg-accent"
          >
            <FileText className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0">
              <span className="block max-w-[12rem] truncate font-medium">{a.name}</span>
              {a.size ? <span className="text-muted-foreground">{formatBytes(a.size)}</span> : null}
            </span>
          </a>
        )
      })}
    </div>
  )
}

function Citations({ message }: { message: Message }) {
  const citations = messageCitations(message)
  if (!citations.length) return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-1" data-testid="citations">
      {citations.map((c) =>
        c.url ? (
          <a
            key={c.n}
            href={c.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded border border-border bg-muted/50 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <sup className="font-semibold">{c.n}</sup>
            <span className="max-w-[10rem] truncate">{c.title}</span>
          </a>
        ) : (
          <span
            key={c.n}
            className="inline-flex items-center gap-1 rounded border border-border bg-muted/50 px-1.5 py-0.5 text-[11px] text-muted-foreground"
          >
            <sup className="font-semibold">{c.n}</sup>
            <span className="max-w-[10rem] truncate">{c.title}</span>
          </span>
        )
      )}
    </div>
  )
}

/** 👍/👎 on AI replies. Selection is seeded from the stored feedback, then kept locally. */
function FeedbackButtons({ message }: { message: Message }) {
  const [selected, setSelected] = useState<'up' | 'down' | null>(null)
  const feedback = useMessageFeedback(message, !message.id.startsWith('temp-'))
  const submit = useSubmitMessageFeedback(message)

  useEffect(() => {
    const mine = feedback.data?.find((f) => f.actor_type === 'user')
    if (mine && (mine.rating === 'up' || mine.rating === 'down')) setSelected(mine.rating)
  }, [feedback.data])

  function rate(rating: 'up' | 'down') {
    setSelected(rating)
    submit.mutate(rating)
  }

  return (
    <div className="flex items-center gap-0.5" data-testid="message-feedback">
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        aria-label="Good response"
        aria-pressed={selected === 'up'}
        onClick={() => rate('up')}
      >
        <ThumbsUp
          className={cn(
            'size-3.5',
            selected === 'up' ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
          )}
        />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="size-6"
        aria-label="Bad response"
        aria-pressed={selected === 'down'}
        onClick={() => rate('down')}
      >
        <ThumbsDown
          className={cn(
            'size-3.5',
            selected === 'down' ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'
          )}
        />
      </Button>
    </div>
  )
}

export function MessageBubble({ message }: { message: Message }) {
  const canWrite = useHasPerm('conversations:write')
  const attachments = (message.attachments ?? []) as unknown as Attachment[]

  // Activity line — centered, muted.
  if (message.visibility === 'activity') {
    return (
      <div className="flex justify-center py-1" data-message-variant="activity">
        <span className="rounded-full bg-muted/60 px-3 py-1 text-xs text-muted-foreground">
          {message.content}
        </span>
      </div>
    )
  }

  const isNote = message.visibility === 'note'
  const alignEnd = !isNote && message.direction === 'out'
  const isAgent = message.author_type === 'agent'
  const isSystem = message.author_type === 'system'
  const showFeedback =
    canWrite && isAgent && message.visibility === 'public' && message.direction === 'out'

  const bubbleClass = isNote
    ? 'bg-amber-100 text-amber-950 border border-amber-300/60 dark:bg-amber-950/40 dark:text-amber-100 dark:border-amber-800/50'
    : alignEnd
      ? 'bg-brand text-brand-foreground'
      : 'bg-muted text-foreground'

  return (
    <div
      className={cn('flex w-full flex-col gap-1', alignEnd ? 'items-end' : 'items-start')}
      data-message-variant={isNote ? 'note' : 'public'}
      data-direction={message.direction}
    >
      <div className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground">
        {!alignEnd ? (
          <Avatar size="sm" className="mr-0.5">
            <AvatarFallback>{initials(message.author_name || '?') || '?'}</AvatarFallback>
          </Avatar>
        ) : null}
        <span className="font-medium text-foreground/80">{message.author_name}</span>
        {isAgent ? (
          <span className="inline-flex items-center gap-0.5 rounded bg-brand-soft px-1 py-px text-[10px] font-semibold text-brand">
            <Bot className="size-2.5" /> AI
          </span>
        ) : null}
        {isSystem ? <span className="text-[10px] uppercase tracking-wide">System</span> : null}
        {isNote ? (
          <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
            <Lock className="size-2.5" /> Internal note
          </span>
        ) : null}
        <span>· {messageTime(message.created_at)}</span>
      </div>

      <div className={cn('max-w-[80%] rounded-2xl px-3.5 py-2 text-sm', bubbleClass)}>
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
        <Attachments attachments={attachments} />
      </div>

      <div className={cn('max-w-[80%]', alignEnd ? 'self-end' : 'self-start')}>
        <Citations message={message} />
      </div>

      {showFeedback ? <FeedbackButtons message={message} /> : null}

      {alignEnd && message.delivery_status ? (
        <span className="px-1 text-[11px] text-muted-foreground">
          {message.delivery_status === 'pending' ? (
            <span className="inline-flex items-center gap-1">
              <Loader2 className="size-3 animate-spin" /> Sending…
            </span>
          ) : message.delivery_status === 'failed' ? (
            <span className="inline-flex items-center gap-1 text-destructive">
              <AlertCircle className="size-3" /> Failed{message.delivery_error ? `: ${message.delivery_error}` : ''}
            </span>
          ) : message.delivery_status === 'sent' ? (
            'Sent'
          ) : null}
        </span>
      ) : null}
    </div>
  )
}
