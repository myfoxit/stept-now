import type { ConversationSummary, WidgetConfig } from '../../types'
import { conversationSubject, isYourTurn, type FederatedResults } from '../api-extra'
import { t } from '../../i18n'
import { timeAgo, trimPreview } from '../format'

/** What a conversation row shows as its one-line teaser: the backend-generated
 * subject when there is one, else the last message trimmed at a word boundary
 * — never a mid-word "Einrich…". */
export function rowPreview(c: ConversationSummary): string {
  const subject = conversationSubject(c)
  if (subject) return trimPreview(subject)
  if (c.last_message_preview) return trimPreview(c.last_message_preview)
  return t('home.conversation')
}

/** Home screen: greeting, federated search, recent conversations, CTAs. */
export function Home({
  config,
  conversations,
  helpCenter,
  search,
  onSearch,
  onOpenConversation,
  onNewConversation,
  onOpenHelp,
  onOpenArticle,
  onStartTour,
}: {
  config: WidgetConfig
  conversations: ConversationSummary[]
  helpCenter: boolean
  /** Federated search results (articles + tours); null while the box is empty. */
  search: FederatedResults | null
  onSearch: (query: string) => void
  onOpenConversation: (id: string) => void
  onNewConversation: () => void
  onOpenHelp: () => void
  onOpenArticle: (slug: string) => void
  onStartTour: (tourId: string) => void
}) {
  // A workspace-authored greeting is already in the workspace's own words, so
  // it wins over ours; only the default falls back to the visitor's language.
  const greeting = config.greeting || t('home.greeting')
  const aiEnabled = Boolean(config.ai_agent_id)
  const searching = Boolean(search)

  return (
    <div class="sw-home">
      <div class="sw-hero">
        <h1 class="sw-hero-title">{greeting}</h1>
        <p class="sw-hero-sub">{t('home.subtitle')}</p>
      </div>

      <div class="sw-home-body">
        <div class="sw-search">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            class="sw-search-input"
            type="search"
            placeholder={t('search.placeholder')}
            value={search?.query ?? ''}
            aria-label={t('search.placeholder')}
            onInput={(e) => onSearch(e.currentTarget.value)}
          />
        </div>

        {searching && search ? (
          <div class="sw-fed-results">
            {search.loading && <div class="sw-loading">{t('app.loading')}</div>}
            {!search.loading && search.articles.length === 0 && search.tours.length === 0 && (
              <div class="sw-empty">{t('help.no_results', { query: search.query })}</div>
            )}
            {search.articles.length > 0 && (
              <div class="sw-fed-group">
                <div class="sw-section-label">{t('search.articles')}</div>
                {search.articles.map((a) => (
                  <button
                    key={a.slug}
                    type="button"
                    class="sw-article-row"
                    onClick={() => onOpenArticle(a.slug)}
                  >
                    <div class="sw-article-title">{a.title}</div>
                    {a.snippet && <div class="sw-article-snippet">{a.snippet}</div>}
                  </button>
                ))}
              </div>
            )}
            {search.tours.length > 0 && (
              <div class="sw-fed-group">
                <div class="sw-section-label">{t('search.tours')}</div>
                {search.tours.map((tour) => (
                  <div key={tour.id} class="sw-tour-row">
                    <div class="sw-tour-row-main">
                      <div class="sw-article-title">{tour.name}</div>
                      {tour.steps > 0 && (
                        <div class="sw-article-snippet">
                          {t('tour.card.steps', { count: tour.steps })}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      class="sw-btn sw-btn-primary sw-tour-row-start"
                      onClick={() => onStartTour(tour.id)}
                    >
                      {t('tour.card.start')}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <button type="button" class="sw-card sw-card-action" onClick={onNewConversation}>
              <div>
                <div class="sw-card-title">{t('home.new_message.title')}</div>
                <div class="sw-card-sub">
                  {aiEnabled ? t('promise.ai') : t('home.new_message.sub_human')}
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
                      <div class={`sw-conv-preview ${c.unread ? 'sw-conv-unread' : ''}`}>
                        {rowPreview(c)}
                        {c.unread && <span class="sw-unread-dot" aria-label={t('home.unread')} />}
                      </div>
                      <div class="sw-conv-time">
                        {timeAgo(c.last_activity_at)}
                        {isYourTurn(c) && (
                          <span class="sw-your-turn">{t('home.your_turn')}</span>
                        )}
                      </div>
                    </div>
                    <span class="sw-card-arrow" aria-hidden="true">
                      →
                    </span>
                  </button>
                ))}
              </div>
            )}
          </>
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
          {t('home.branding')}
        </a>
      </div>
    </div>
  )
}
