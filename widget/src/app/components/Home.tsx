import type { ConversationSummary, WidgetConfig } from '../../types'
import { timeAgo } from '../format'

/** Home screen: greeting, recent conversations, new-message + help CTAs. */
export function Home({
  config,
  conversations,
  helpCenter,
  onOpenConversation,
  onNewConversation,
  onOpenHelp,
}: {
  config: WidgetConfig
  conversations: ConversationSummary[]
  helpCenter: boolean
  onOpenConversation: (id: string) => void
  onNewConversation: () => void
  onOpenHelp: () => void
}) {
  const greeting = config.greeting || 'Hi there 👋'
  const aiEnabled = Boolean(config.ai_agent_id)

  return (
    <div class="sw-home">
      <div class="sw-hero">
        <h1 class="sw-hero-title">{greeting}</h1>
        <p class="sw-hero-sub">How can we help you today?</p>
      </div>

      <div class="sw-home-body">
        <button type="button" class="sw-card sw-card-action" onClick={onNewConversation}>
          <div>
            <div class="sw-card-title">Send us a message</div>
            <div class="sw-card-sub">
              {aiEnabled
                ? 'Ask us anything — our AI answers instantly.'
                : 'We typically reply within a few minutes.'}
            </div>
          </div>
          <span class="sw-card-arrow" aria-hidden="true">
            →
          </span>
        </button>

        {helpCenter && (
          <button type="button" class="sw-card sw-card-action" onClick={onOpenHelp}>
            <div>
              <div class="sw-card-title">Search for help</div>
              <div class="sw-card-sub">Browse articles and answers.</div>
            </div>
            <span class="sw-card-arrow" aria-hidden="true">
              →
            </span>
          </button>
        )}

        {conversations.length > 0 && (
          <div class="sw-recent">
            <div class="sw-section-label">Recent conversations</div>
            {conversations.map((c) => (
              <button
                key={c.id}
                type="button"
                class="sw-conv-row"
                onClick={() => onOpenConversation(c.id)}
              >
                <div class="sw-conv-main">
                  <div class="sw-conv-preview">
                    {c.last_message_preview || 'Conversation'}
                    {c.unread && <span class="sw-unread-dot" aria-label="unread" />}
                  </div>
                  <div class="sw-conv-time">{timeAgo(c.last_activity_at)}</div>
                </div>
                <span class="sw-card-arrow" aria-hidden="true">
                  →
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* The only Stept branding in the widget — everything above it belongs to
          the customer. Mono and inheriting the muted text colour so it stays a
          credit rather than a second logo competing with theirs, and drawn in
          the optically corrected small geometry (assets/brand/README.md: the
          24px master closes up below 20px). */}
      <div class="sw-branding">
        <a href="https://stepped.ai" target="_blank" rel="noopener noreferrer">
          <svg
            class="sw-brand-mark"
            viewBox="0 0 32 32"
            width="13"
            height="13"
            aria-hidden="true"
            focusable="false"
          >
            <rect x="4" y="5" width="20" height="9" rx="3.8" fill="currentColor" />
            <rect x="8" y="18" width="20" height="9" rx="3.8" fill="currentColor" />
          </svg>
          Powered by Stept
        </a>
      </div>
    </div>
  )
}
