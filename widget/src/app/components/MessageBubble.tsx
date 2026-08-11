import { useEffect, useRef, useState } from 'preact/hooks'

import type { FeedbackRating } from '../../types'
import type { UiMessage } from '../controller'
import { clockTime, initials } from '../format'
import { renderMarkdown } from '../md'
import { t } from '../../i18n'

/** localStorage key remembering the visitor's rating for one answer. */
export function feedbackStorageKey(widgetKey: string, messageId: string): string {
  return `stept:${widgetKey}:fb:${messageId}`
}

function storedRating(widgetKey: string, messageId: string): FeedbackRating | null {
  try {
    const v = window.localStorage.getItem(feedbackStorageKey(widgetKey, messageId))
    return v === 'up' || v === 'down' ? v : null
  } catch {
    return null
  }
}

function storeRating(widgetKey: string, messageId: string, rating: FeedbackRating): void {
  try {
    window.localStorage.setItem(feedbackStorageKey(widgetKey, messageId), rating)
  } catch {
    /* private mode — session only */
  }
}

function ThumbIcon({ down = false }: { down?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      {down ? (
        <>
          <path d="M17 14V2" />
          <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
        </>
      ) : (
        <>
          <path d="M7 10v12" />
          <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
        </>
      )}
    </svg>
  )
}

/** A single chat bubble. Visitor messages sit right; agent/AI/user left. */
export function MessageBubble({
  message,
  widgetKey = '',
  onFeedback,
  onRetry,
}: {
  message: UiMessage
  /** Namespaces the persisted thumb state; required for feedback to render. */
  widgetKey?: string
  /** Present on ratable threads: posts the rating (AI answers only). */
  onFeedback?: (messageId: string, rating: FeedbackRating) => void
  /** Present when a failed send can be retried. */
  onRetry?: (messageId: string) => void
}) {
  const mine = message.direction === 'in'
  const system = message.author_type === 'system'
  const citations = message.meta?.citations ?? []

  // AI answers (public outbound agent messages) get the feedback thumbs.
  const ratable =
    Boolean(onFeedback) &&
    message.author_type === 'agent' &&
    message.direction === 'out' &&
    !message.pending &&
    !message.failed

  const [rating, setRating] = useState<FeedbackRating | null>(() =>
    ratable ? storedRating(widgetKey, message.id) : null,
  )
  const [thanks, setThanks] = useState(false)
  const thanksTimer = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (thanksTimer.current !== null) window.clearTimeout(thanksTimer.current)
    },
    [],
  )

  const rate = (value: FeedbackRating): void => {
    if (rating === value) return // backend upserts; switching allowed, repeats are a no-op
    setRating(value)
    storeRating(widgetKey, message.id, value)
    onFeedback?.(message.id, value)
    setThanks(true)
    if (thanksTimer.current !== null) window.clearTimeout(thanksTimer.current)
    thanksTimer.current = window.setTimeout(() => setThanks(false), 2500)
  }

  if (system) {
    return (
      <div class="sw-activity">
        <span dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
      </div>
    )
  }

  return (
    <div class={`sw-row ${mine ? 'sw-row-mine' : 'sw-row-them'}`}>
      {!mine && (
        <div class="sw-avatar" aria-hidden="true">
          {initials(message.author_name || t('message.agent'))}
        </div>
      )}
      <div class="sw-bubble-wrap">
        <div
          class={`sw-bubble ${mine ? 'sw-bubble-mine' : 'sw-bubble-them'} ${
            message.failed ? 'sw-bubble-failed' : ''
          } ${message.pending ? 'sw-bubble-pending' : ''}`}
        >
          <div class="sw-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
          {citations.length > 0 && (
            <div class="sw-citations">
              {citations.map((c) =>
                c.url ? (
                  <a
                    key={c.n}
                    class="sw-cite"
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={c.title || c.url}
                  >
                    [{c.n}] {c.title || t('message.source')}
                  </a>
                ) : (
                  <span key={c.n} class="sw-cite">
                    [{c.n}] {c.title || t('message.source')}
                  </span>
                ),
              )}
            </div>
          )}
        </div>
        {ratable && (
          <div class="sw-fb">
            <button
              type="button"
              class={`sw-fb-btn ${rating === 'up' ? 'sw-fb-on' : ''}`}
              aria-label={t('message.helpful')}
              aria-pressed={rating === 'up'}
              onClick={() => rate('up')}
            >
              <ThumbIcon />
            </button>
            <button
              type="button"
              class={`sw-fb-btn ${rating === 'down' ? 'sw-fb-on' : ''}`}
              aria-label={t('message.not_helpful')}
              aria-pressed={rating === 'down'}
              onClick={() => rate('down')}
            >
              <ThumbIcon down />
            </button>
            {thanks && (
              <span class="sw-fb-thanks" role="status">
                Thanks for the feedback
              </span>
            )}
          </div>
        )}
        <div class="sw-meta">
          {message.failed ? (
            <span class="sw-failed">
              Not delivered
              {onRetry && (
                <>
                  {' · '}
                  <button type="button" class="sw-retry" onClick={() => onRetry(message.id)}>
                    Retry
                  </button>
                </>
              )}
            </span>
          ) : message.pending ? (
            <span>{t('message.sending')}</span>
          ) : (
            <span>{clockTime(message.created_at)}</span>
          )}
        </div>
      </div>
    </div>
  )
}
