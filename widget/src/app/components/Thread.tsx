import { useEffect, useRef } from 'preact/hooks'

import type { FeedbackRating } from '../../types'
import type { PendingActionCard, UiMessage } from '../controller'
import type { Starter, TourState } from '../api-extra'
import { ActionCard } from './ActionCard'
import { Composer } from './Composer'
import { Csat } from './Csat'
import { MessageBubble } from './MessageBubble'
import { PageAssist } from './PageAssist'
import { TypingDots } from './TypingDots'
import { t } from '../../i18n'

/** The live tour-state system line ("▶ Started …"), or the blocked hint. */
function TourStateLine({
  state,
  onResume,
}: {
  state: TourState
  onResume: (tourId: string) => void
}) {
  switch (state.status) {
    case 'started':
    case 'step_viewed':
    case 'step':
    case 'running': {
      const progress =
        state.step !== null && state.total !== null
          ? t('tour.state.progress', { step: state.step, total: state.total })
          : ''
      const started = state.title
        ? t('tour.state.started', { title: state.title })
        : t('tour.state.started_plain')
      return <div class="sw-activity">▶ {progress ? `${started} · ${progress}` : started}</div>
    }
    case 'completed':
      return (
        <div class="sw-activity">
          ✓{' '}
          {state.title
            ? t('tour.state.completed', { title: state.title })
            : t('tour.state.completed_plain')}
        </div>
      )
    case 'dismissed':
      return (
        <div class="sw-activity">
          ✕{' '}
          {state.step !== null
            ? t('tour.state.dismissed', { step: state.step })
            : t('tour.state.dismissed_plain')}
        </div>
      )
    case 'blocked':
    case 'step_error':
      // Quiet UI hint only — never a fabricated agent message.
      return (
        <div class="sw-tour-hint" role="status">
          {t('tour.state.blocked')}
          {state.tourId && (
            <button type="button" class="sw-linklike" onClick={() => onResume(state.tourId)}>
              {t('tour.state.resume')}
            </button>
          )}
        </div>
      )
    default:
      return null
  }
}

export function Thread({
  messages,
  agentTyping,
  hasMore,
  loading,
  status,
  csatDone,
  greeting,
  widgetKey,
  aiEnabled,
  agentName,
  aiDisclosure,
  starters,
  tourState,
  humanRequested,
  pageControl,
  pageTitle,
  actionsAllowed,
  workingOnPage,
  pendingAction,
  onSend,
  onTyping,
  onLoadMore,
  onCsat,
  onFeedback,
  onRetry,
  onStartTour,
  onResumeTour,
  onRequestHuman,
  onAllowActions,
  onRunAction,
  onDismissAction,
}: {
  messages: UiMessage[]
  agentTyping: boolean
  hasMore: boolean
  loading: boolean
  status: string | null
  csatDone: boolean
  greeting: string
  widgetKey: string
  /** The workspace has an AI agent wired to this inbox. */
  aiEnabled: boolean
  /** Boot-config AI persona name (fallback for agent messages). */
  agentName: string | null
  /** Show the "AI" chip on agent answers. */
  aiDisclosure: boolean
  /** Suggested openers for an empty conversation (null while loading). */
  starters: Starter[] | null
  /** Live tour progress, rendered as a system line at the tail. */
  tourState: TourState | null
  /** The visitor already asked for a human in this conversation. */
  humanRequested: boolean
  /** The workspace's agent can see this page — show the assist row. */
  pageControl: boolean
  pageTitle: string
  actionsAllowed: boolean
  workingOnPage: string | null
  /** A client action waiting for the visitor's go-ahead (confirm: true). */
  pendingAction: PendingActionCard | null
  onSend: (text: string) => void
  onTyping: (isTyping: boolean) => void
  onLoadMore: () => void
  onCsat: (rating: number, feedback?: string) => void
  onFeedback: (messageId: string, rating: FeedbackRating) => void
  onRetry: (messageId: string) => void
  onStartTour: (tourId: string) => void
  onResumeTour: (tourId: string) => void
  onRequestHuman: () => void
  onAllowActions: (allowed: boolean) => void
  onRunAction: () => void
  onDismissAction: () => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)

  useEffect(() => {
    if (atBottom.current && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight
    }
  }, [messages, agentTyping, pendingAction, tourState])

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
            {t('thread.load_earlier')}
          </button>
        )}
        {loading && messages.length === 0 && <div class="sw-loading">{t('app.loading')}</div>}
        {!loading && messages.length === 0 && (
          <div class="sw-thread-intro">
            <p>{greeting}</p>
            {/* One promise, the same one Home makes — not "we'll get back to
                you" here and "answers instantly" there. */}
            <p class="sw-muted">{aiEnabled ? t('promise.ai') : t('thread.intro_hint')}</p>
            {starters && starters.length > 0 && (
              <div class="sw-starters">
                <div class="sw-starters-label">{t('starters.label')}</div>
                {starters.map((s) => (
                  <button
                    key={s.text}
                    type="button"
                    class="sw-chip"
                    onClick={() => onSend(s.text)}
                  >
                    {s.kind === 'tour' && (
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
                        <path d="M8 5v14l11-7z" />
                      </svg>
                    )}
                    {s.text}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            widgetKey={widgetKey}
            agentName={agentName}
            aiDisclosure={aiDisclosure}
            onFeedback={onFeedback}
            onRetry={onRetry}
            onStartTour={onStartTour}
          />
        ))}
        {tourState && <TourStateLine state={tourState} onResume={onResumeTour} />}
        {agentTyping && <TypingDots />}
        {pendingAction && (
          <ActionCard action={pendingAction} onRun={onRunAction} onDismiss={onDismissAction} />
        )}
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
      {/* The way out of the AI loop. Low-key, but always there. */}
      <div class="sw-handoff">
        {humanRequested ? (
          <span class="sw-handoff-pending" role="status">
            {t('handoff.pending')}
          </span>
        ) : (
          <button type="button" class="sw-linklike" onClick={onRequestHuman}>
            {t('handoff.button')}
          </button>
        )}
      </div>
    </div>
  )
}
