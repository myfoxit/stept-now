/**
 * Knowledge base + help-center API layer.
 * Thin typed wrappers over the workspace-scoped REST endpoints; request/response
 * shapes mirror backend `schemas/knowledge.py` and `schemas/articles.py`.
 */

import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Source = components['schemas']['SourceOut']
export type Document = components['schemas']['DocumentOut']
export type DocumentDetail = components['schemas']['DocumentDetailOut']
export type ChunkPreview = components['schemas']['ChunkPreviewOut']
export type SearchResult = components['schemas']['SearchResponse']
export type RetrievedChunk = components['schemas']['RetrievedChunkOut']
export type Collection = components['schemas']['CollectionOut']
export type Article = components['schemas']['ArticleOut']
export type ArticleListItem = components['schemas']['ArticleListItem']
export type DocumentsPage = components['schemas']['OffsetPage_DocumentOut_']
export type ArticlesPage = components['schemas']['OffsetPage_ArticleListItem_']
export type SearchAnalyticsOverview = components['schemas']['SearchAnalyticsOverview']
export type AnalyticsQueryStats = components['schemas']['QueryStats']
export type AnalyticsQueriesPerDay = components['schemas']['QueriesPerDay']
export type AnalyticsQueriesBySource = components['schemas']['QueriesBySource']
export type AnalyticsTopQuery = components['schemas']['TopQuery']
export type AnalyticsZeroResultQuery = components['schemas']['ZeroResultQuery']
export type AnalyticsFeedbackStats = components['schemas']['FeedbackStats']
export type AnalyticsAiStats = components['schemas']['AiStats']

export type SourceType = components['schemas']['SourceCreate']['type']
export type DocStatus = 'pending' | 'processing' | 'indexed' | 'failed'
export type SourceStatus = 'idle' | 'syncing' | 'error'
export type ArticleStatus = 'draft' | 'published'

/** Source types whose content lives elsewhere and can be (re-)synced / auto-refreshed. */
export const REMOTE_SOURCE_TYPES: readonly string[] = [
  'urls',
  'sitemap',
  'crawl',
  'github',
  'notion',
  'articles',
]

export const knowledgeKeys = {
  sources: (workspaceId: string) => ['knowledge', workspaceId, 'sources'] as const,
  source: (workspaceId: string, id: string) => ['knowledge', workspaceId, 'source', id] as const,
  documents: (workspaceId: string, sourceId?: string, status?: string) =>
    ['knowledge', workspaceId, 'documents', sourceId ?? null, status ?? null] as const,
  document: (workspaceId: string, id: string) =>
    ['knowledge', workspaceId, 'document', id] as const,
  collections: (workspaceId: string) => ['knowledge', workspaceId, 'collections'] as const,
  articles: (workspaceId: string, collectionId?: string | null, status?: string | null) =>
    ['knowledge', workspaceId, 'articles', collectionId ?? null, status ?? null] as const,
  article: (workspaceId: string, id: string) => ['knowledge', workspaceId, 'article', id] as const,
  analytics: (workspaceId: string, days: number) =>
    ['knowledge-analytics', workspaceId, days] as const,
}

export const knowledgeApi = {
  // --- sources -------------------------------------------------------------
  listSources: () => api.get<Source[]>(ws('/knowledge/sources')),
  getSource: (id: string) => api.get<Source>(ws(`/knowledge/sources/${id}`)),
  createSource: (body: {
    type: SourceType
    name: string
    config?: Record<string, unknown>
    secrets?: Record<string, string>
  }) => api.post<Source>(ws('/knowledge/sources'), { config: {}, ...body }),
  updateSource: (
    id: string,
    body: {
      name?: string
      config?: Record<string, unknown>
      secrets?: Record<string, string>
    }
  ) => api.patch<Source>(ws(`/knowledge/sources/${id}`), body),
  deleteSource: (id: string) => api.delete<{ message: string }>(ws(`/knowledge/sources/${id}`)),
  syncSource: (id: string) => api.post<Source>(ws(`/knowledge/sources/${id}/sync`)),

  // --- documents -----------------------------------------------------------
  listDocuments: (params: { sourceId?: string; status?: string; limit?: number; offset?: number }) =>
    api.get<DocumentsPage>(ws('/knowledge/documents'), {
      query: {
        source_id: params.sourceId,
        status: params.status,
        limit: params.limit,
        offset: params.offset,
      },
    }),
  getDocument: (id: string) => api.get<DocumentDetail>(ws(`/knowledge/documents/${id}`)),
  deleteDocument: (id: string) =>
    api.delete<{ message: string }>(ws(`/knowledge/documents/${id}`)),
  retryDocument: (id: string) => api.post<Document>(ws(`/knowledge/documents/${id}/retry`)),
  addTextDocument: (sourceId: string, body: { title: string; content: string }) =>
    api.post<Document>(ws(`/knowledge/sources/${sourceId}/documents`), body),
  uploadDocument: (sourceId: string, file: File) =>
    api.upload<Document>(ws(`/knowledge/sources/${sourceId}/documents`), file),

  // --- search --------------------------------------------------------------
  search: (body: { query: string; k?: number; source_ids?: string[] | null; rerank?: boolean }) =>
    api.post<SearchResult>(ws('/knowledge/search'), body),

  // --- analytics -----------------------------------------------------------
  analytics: (days: number) =>
    api.get<SearchAnalyticsOverview>(ws('/knowledge/analytics'), { query: { days } }),

  // --- collections + articles ---------------------------------------------
  listCollections: () => api.get<Collection[]>(ws('/articles/collections')),
  createCollection: (body: { name: string; icon?: string | null; description?: string | null }) =>
    api.post<Collection>(ws('/articles/collections'), body),
  updateCollection: (
    id: string,
    body: { name?: string; icon?: string | null; description?: string | null }
  ) => api.patch<Collection>(ws(`/articles/collections/${id}`), body),
  deleteCollection: (id: string) =>
    api.delete<{ message: string }>(ws(`/articles/collections/${id}`)),

  listArticles: (params: { collectionId?: string | null; status?: string | null } = {}) =>
    api.get<ArticlesPage>(ws('/articles'), {
      query: { collection_id: params.collectionId, status: params.status, limit: 200 },
    }),
  getArticle: (id: string) => api.get<Article>(ws(`/articles/${id}`)),
  createArticle: (body: { title: string; body?: string; collection_id?: string | null }) =>
    api.post<Article>(ws('/articles'), body),
  updateArticle: (
    id: string,
    body: { title?: string; body?: string; collection_id?: string | null }
  ) => api.patch<Article>(ws(`/articles/${id}`), body),
  deleteArticle: (id: string) => api.delete<{ message: string }>(ws(`/articles/${id}`)),
  publishArticle: (id: string) => api.post<Article>(ws(`/articles/${id}/publish`)),
  unpublishArticle: (id: string) => api.post<Article>(ws(`/articles/${id}/unpublish`)),
}
