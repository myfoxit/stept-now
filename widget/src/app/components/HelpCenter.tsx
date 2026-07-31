import type { WidgetArticlesResponse } from '../../types'

/** Help-center browser: search box + collections tree or search results. */
export function HelpCenter({
  articles,
  query,
  onSearch,
  onOpen,
}: {
  articles: WidgetArticlesResponse | null
  query: string
  onSearch: (q: string) => void
  onOpen: (slug: string) => void
}) {
  const searching = query.trim().length > 0

  return (
    <div class="sw-help">
      <div class="sw-search">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          class="sw-search-input"
          type="search"
          placeholder="Search for help"
          value={query}
          aria-label="Search help articles"
          onInput={(e) => onSearch(e.currentTarget.value)}
        />
      </div>

      {!articles && <div class="sw-loading">Loading…</div>}

      {articles && searching && (
        <div class="sw-articles">
          {articles.results.length === 0 ? (
            <div class="sw-empty">No results for “{query}”.</div>
          ) : (
            articles.results.map((r) => (
              <button key={r.slug} type="button" class="sw-article-row" onClick={() => onOpen(r.slug)}>
                <div class="sw-article-title">{r.title}</div>
                <div class="sw-article-snippet">{r.snippet}</div>
              </button>
            ))
          )}
        </div>
      )}

      {articles && !searching && (
        <div class="sw-collections">
          {articles.collections.length === 0 ? (
            <div class="sw-empty">No articles yet.</div>
          ) : (
            articles.collections.map((col) => (
              <div key={col.slug} class="sw-collection">
                <div class="sw-collection-head">
                  {col.icon && <span class="sw-collection-icon">{col.icon}</span>}
                  <span>{col.name}</span>
                </div>
                {col.articles.map((a) => (
                  <button key={a.slug} type="button" class="sw-article-row" onClick={() => onOpen(a.slug)}>
                    <div class="sw-article-title">{a.title}</div>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
