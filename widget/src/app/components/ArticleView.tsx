import type { ArticleDetail } from '../../types'
import { renderMarkdown } from '../md'

export function ArticleView({
  article,
  loading,
}: {
  article: ArticleDetail | null
  loading: boolean
}) {
  if (loading || !article) {
    return <div class="sw-loading">Loading…</div>
  }
  return (
    <article class="sw-article">
      {article.collection && <div class="sw-article-crumb">{article.collection.name}</div>}
      <h1 class="sw-article-heading">{article.title}</h1>
      <div class="sw-md sw-article-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(article.body) }} />
    </article>
  )
}
