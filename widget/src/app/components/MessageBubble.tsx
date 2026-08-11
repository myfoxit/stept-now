import { useEffect, useRef, useState } from 'preact/hooks'

import type { FeedbackRating } from '../../types'
import type { UiMessage } from '../controller'
import { tourEvent, tourOffer, type TourEventAttachment } from '../api-extra'
import { clockTime, initials } from '../format'
import { renderMarkdown } from '../md'
import { t } from '../../i18n'
import { TourCard } from './TourCard'

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

/** "▶ Started …" / "✓ Completed …" / "✕ Dismissed at step 2" for a tour_event
 * message. The glyph is not language; the sentence is. */
export function tourEventLine(ev: TourEventAttachment): string {
  switch (ev.event) {
    case 'started':
      return `▶ ${ev.title ? t('tour.state.started', { title: ev.title }) : t('tour.state.started_plain')}`
    case 'completed':
      return `✓ ${ev.title ? t('tour.state.completed', { title: ev.title }) : t('tour.state.completed_plain')}`
    case 'dismissed':
      return `✕ ${
        typeof ev.step === 'number'
          ? t('tour.state.dismissed', { step: ev.step })
          : t('tour.state.dismissed_plain')
      }`
    default:
      return ''
  }
}

/** A single chat bubble. Visitor messages sit right; agent/AI/user left. */
export function MessageBubble({
  message,
  widgetKey = '',
  agentName,
  aiDisclosure = true,
  onFeedback,
  onRetry,
  onStartTour,
}: {
  message: UiMessage
  /** Namespaces the persisted thumb state; required for feedback to render. */
  widgetKey?: string
  /** Boot-config agent persona — the fallback when the message has no author. */
  agentName?: string | null
  /** Render the "AI" chip on agent answers (boot `ai_disclosure`, default on). */
  aiDisclosure?: boolean
  /** Present on ratable threads: posts the rating (AI answers only). */
  onFeedback?: (messageId: string, rating: FeedbackRating) => void
  /** Present when a failed send can be retried. */
  onRetry?: (messageId: string) => void
  /** Starts a tour offered by a `tour_offer` attachment. */
  onStartTour?: (tourId: string) => void
}) {
  const mine = message.direction === 'in'
  const system = message.author_type === 'system'
  const citations = message.meta?.citations ?? []
  const tourEv = tourEvent(message)
  const offer = tourOffer(message)

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

  // Tour lifecycle telemetry reads as ambient activity, not as someone talking.
  if (tourEv) {
    return <div class="sw-activity">{tourEventLine(tourEv) || message.content}</div>
  }

  if (system) {
    return (
      <div class="sw-activity">
        <span dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
      </div>
    )
  }

  // Who is talking: the message's own author, else the configured AI persona.
  const displayName = message.author_name || (message.author_type === 'agent' ? agentName : '') || ''
  const showAiChip = message.author_type === 'agent' && aiDisclosure

  return (
    <div class={`sw-row ${mine ? 'sw-row-mine' : 'sw-row-them'}`}>
      {!mine && (
        <div class="sw-avatar" aria-hidden="true">
          {initials(displayName || t('message.agent'))}
        </div>
      )}
      <div class="sw-bubble-wrap">
        {!mine && (displayName || showAiChip) && (
          <div class="sw-author">
            {displayName || t('message.agent')}
            {showAiChip && <span class="sw-ai-chip">{t('message.ai')}</span>}
          </div>
        )}
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
        {offer && onStartTour && <TourCard offer={offer} onStart={onStartTour} />}
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
                {t('message.feedback_thanks')}
              </span>
            )}
          </div>
        )}
        <div class="sw-meta">
          {message.failed ? (
            <span class="sw-failed">
              {t('message.not_delivered')}
              {onRetry && (
                <>
                  {' · '}
                  <button type="button" class="sw-retry" onClick={() => onRetry(message.id)}>
                    {t('message.retry')}
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
