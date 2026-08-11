import type { ConversationSummary, WidgetConfig } from '../../types'
import { t } from '../../i18n'
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
  // A workspace-authored greeting is already in the workspace's own words, so
  // it wins over ours; only the default falls back to the visitor's language.
  const greeting = config.greeting || t('home.greeting')
  const aiEnabled = Boolean(config.ai_agent_id)

  return (
    <div class="sw-home">
      <div class="sw-hero">
        <h1 class="sw-hero-title">{greeting}</h1>
        <p class="sw-hero-sub">{t('home.subtitle')}</p>
      </div>

      <div class="sw-home-body">
        <button type="button" class="sw-card sw-card-action" onClick={onNewConversation}>
          <div>
            <div class="sw-card-title">{t('home.new_message.title')}</div>
            <div class="sw-card-sub">
              {aiEnabled ? t('home.new_message.sub_ai') : t('home.new_message.sub_human')}
            </div>
          </div>
          <span class="sw-card-arrow" aria-hidden="true">
            →
          </span>
        </button>

        {helpCenter && (
          <button type="button" class="sw-card sw-card-action" onClick={onOpenHelp}>
            <div>
              <div class="sw-card-title">{t('home.help.title')}</div>
              <div class="sw-card-sub">{t('home.help.sub')}</div>
            </div>
            <span class="sw-card-arrow" aria-hidden="true">
              →
            </span>
          </button>
        )}

        {conversations.length > 0 && (
          <div class="sw-recent">
            <div class="sw-section-label">{t('home.recent')}</div>
            {conversations.map((c) => (
              <button
                key={c.id}
                type="button"
                class="sw-conv-row"
                onClick={() => onOpenConversation(c.id)}
              >
                <div class="sw-conv-main">
                  <div class="sw-conv-preview">
                    {c.last_message_preview || t('home.conversation')}
                    {c.unread && <span class="sw-unread-dot" aria-label={t('home.unread')} />}
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

      <div class="sw-branding">
        <a href="https://stepped.ai" target="_blank" rel="noopener noreferrer">
          {t('home.branding')}
        </a>
      </div>
    </div>
  )
}
