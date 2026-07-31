/** TanStack Query hooks for the knowledge base + help center. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { currentWorkspaceId } from '@/stores/auth'

import { knowledgeApi, knowledgeKeys } from './api'

/** Sources refetch on an interval while any source is still syncing. */
export function useSources() {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.sources(workspaceId),
    queryFn: knowledgeApi.listSources,
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.status === 'syncing') ? 3000 : false,
  })
}

export function useSource(id: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.source(workspaceId, id),
    queryFn: () => knowledgeApi.getSource(id),
    enabled: Boolean(id),
  })
}

/** Documents poll while anything is still pending/processing. */
export function useDocuments(sourceId?: string, status?: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.documents(workspaceId, sourceId, status),
    queryFn: () => knowledgeApi.listDocuments({ sourceId, status, limit: 200 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => d.status === 'pending' || d.status === 'processing')
        ? 2500
        : false,
  })
}

export function useDocument(id: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.document(workspaceId, id),
    queryFn: () => knowledgeApi.getDocument(id),
    enabled: Boolean(id),
  })
}

export function useCollections() {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.collections(workspaceId),
    queryFn: knowledgeApi.listCollections,
  })
}

export function useArticles(collectionId?: string | null, status?: string | null) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.articles(workspaceId, collectionId, status),
    queryFn: () => knowledgeApi.listArticles({ collectionId, status }),
  })
}

export function useArticle(id: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.article(workspaceId, id),
    queryFn: () => knowledgeApi.getArticle(id),
    enabled: Boolean(id),
  })
}

/** Invalidate every knowledge query for the active workspace. */
export function useInvalidateKnowledge() {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['knowledge', workspaceId] })
}

export function useSearch() {
  return useMutation({
    mutationFn: (vars: {
      query: string
      k?: number
      source_ids?: string[] | null
      rerank?: boolean
    }) => knowledgeApi.search(vars),
  })
}

/** Search-analytics overview for the last N days (7/30/90). */
export function useKnowledgeAnalytics(days: number) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: knowledgeKeys.analytics(workspaceId, days),
    queryFn: () => knowledgeApi.analytics(days),
  })
}
