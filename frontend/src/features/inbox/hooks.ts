/**
 * Inbox data hooks + realtime cache wiring.
 *
 * The cache mutators (applyMessageCreated / applyConversationUpdated) are pure
 * over a QueryClient so they can be unit-tested by dispatching a fake event.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { useRealtime } from '@/api/ws'
import { useAuthStore } from '@/stores/auth'
import type { CursorPage } from '@/features/contacts/api'
import { macrosApi, type MacroRunOut } from '@/features/automation/api'
import {
  approvalsApi,
  bulkApi,
  collaborationApi,
  copilotSuggest,
  feedbackApi,
  inboxApi,
  listCanned,
  listInboxes,
  listMembers,
  listTeams,
  slaApi,
  viewsApi,
  workingHoursApi,
  type AttachmentRef,
  type Conversation,
  type ConversationFilters,
  type ConversationListItem,
  type ConversationPatch,
  type FilterQuery,
  type Message,
} from '@/features/inbox/api'
import { t } from '@/i18n'

const AREA = 'inbox'

/** Surface the API's own message when there is one; fall back otherwise. */
function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

type MsgInfinite = { pages: CursorPage<Message>[]; pageParams: unknown[] }
type ListInfinite = { pages: CursorPage<ConversationListItem>[]; pageParams: unknown[] }

// --- queries ----------------------------------------------------------------

export function useCounts() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'counts'],
    enabled: !!workspaceId,
    queryFn: inboxApi.counts,
  })
}

/**
 * The conversation feed. An inline `query` (a report drill-down) routes to
 * `POST /conversations/search` instead of the list endpoint, so the drilled
 * list is produced by exactly the query the report row counted.
 */
export function useConversationsList(filters: ConversationFilters, query?: FilterQuery | null) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useInfiniteQuery({
    queryKey: [AREA, workspaceId, 'conversations', filters, query ?? null],
    enabled: !!workspaceId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      query ? viewsApi.search(query, pageParam) : inboxApi.listConversations(filters, pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function useConversation(conversationId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'conversation', conversationId],
    enabled: !!workspaceId && !!conversationId,
    queryFn: () => inboxApi.get(conversationId!),
  })
}

export function useMessages(conversationId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useInfiniteQuery({
    queryKey: [AREA, workspaceId, 'messages', conversationId],
    enabled: !!workspaceId && !!conversationId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => inboxApi.listMessages(conversationId!, pageParam),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function useInboxes() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'inboxes'],
    enabled: !!workspaceId,
    queryFn: listInboxes,
  })
}

export function useCanned() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'canned'],
    enabled: !!workspaceId,
    queryFn: listCanned,
  })
}

export function useMembers() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'members'],
    enabled: !!workspaceId,
    queryFn: listMembers,
  })
}

export function useTeams() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'teams'],
    enabled: !!workspaceId,
    queryFn: listTeams,
  })
}

export function usePendingApprovals() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'approvals'],
    enabled: !!workspaceId,
    queryFn: approvalsApi.listPending,
  })
}

// --- sla --------------------------------------------------------------------

export function useConversationSla(conversationId: string | undefined) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: ['sla', workspaceId, conversationId],
    enabled: !!workspaceId && !!conversationId,
    queryFn: () => slaApi.get(conversationId!),
  })
}

/** SLA policies available to apply (small list; shared 'sla' area cache). */
export function useSlaPolicyOptions(enabled = true) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: ['sla', workspaceId, 'policies'],
    enabled: !!workspaceId && enabled,
    queryFn: slaApi.listPolicies,
  })
}

export function useApplySla(conversationId: string) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: (slaPolicyId: string | null) => slaApi.apply(conversationId, slaPolicyId),
    onSuccess: (_out, slaPolicyId) => {
      toast.success(slaPolicyId ? 'SLA applied' : 'SLA removed')
      qc.invalidateQueries({ queryKey: ['sla', workspaceId, conversationId] })
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update SLA'),
  })
}

// --- macros (run from the inbox) --------------------------------------------

/** Macros available to run; keyed with the automation area so both share cache. */
export function useRunnableMacros(enabled = true) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: ['automation', workspaceId, 'macros'],
    enabled: !!workspaceId && enabled,
    queryFn: macrosApi.list,
  })
}

export function useRunMacro(conversationId: string) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: (macroId: string) => macrosApi.run(macroId, conversationId),
    onSuccess: (out: MacroRunOut) => {
      const failed = out.results.filter((r) => !r.ok)
      if (failed.length === 0) {
        toast.success(t('inbox.macro_applied'))
      } else {
        toast.error('Macro partially applied', {
          description: `Failed: ${failed
            .map((f) => `${f.action}${f.error ? ` (${f.error})` : ''}`)
            .join(', ')}`,
        })
      }
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversation', conversationId] })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'messages', conversationId] })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
      qc.invalidateQueries({ queryKey: ['sla', workspaceId, conversationId] })
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not run macro'),
  })
}

// --- message feedback -------------------------------------------------------

export function useMessageFeedback(
  message: Pick<Message, 'id' | 'conversation_id'>,
  enabled: boolean
) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'feedback', message.id],
    enabled: !!workspaceId && enabled,
    queryFn: () => feedbackApi.list(message.conversation_id, message.id),
  })
}

export function useSubmitMessageFeedback(message: Pick<Message, 'id' | 'conversation_id'>) {
  return useMutation({
    mutationFn: (rating: 'up' | 'down') =>
      feedbackApi.submit(message.conversation_id, message.id, rating),
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not record feedback'),
  })
}

/** Newest-page-first pages → a single ascending list for the timeline. */
export function flattenMessages(data: MsgInfinite | undefined): Message[] {
  if (!data) return []
  return data.pages
    .slice()
    .reverse()
    .flatMap((p) => p.items)
}

// --- cache mutation helpers -------------------------------------------------

function appendToThread(data: MsgInfinite | undefined, message: Message): MsgInfinite {
  if (!data || data.pages.length === 0) {
    return { pages: [{ items: [message], next_cursor: null }], pageParams: [undefined] }
  }
  if (data.pages.some((p) => p.items.some((m) => m.id === message.id))) return data
  const pages = data.pages.slice()
  pages[0] = { ...pages[0], items: [...pages[0].items, message] }
  return { ...data, pages }
}

/** message.created realtime handler. Appends to the open thread + bumps list. */
export function applyMessageCreated(
  qc: QueryClient,
  workspaceId: string | null,
  openConversationId: string | undefined,
  data: Record<string, unknown>
) {
  const message = data.message as Message
  const summary = (data.conversation ?? {}) as Record<string, unknown>
  const convId = message.conversation_id

  if (convId === openConversationId) {
    qc.setQueryData<MsgInfinite>([AREA, workspaceId, 'messages', convId], (old) =>
      appendToThread(old, message)
    )
  }

  let bumped = false
  qc.setQueriesData<ListInfinite>({ queryKey: [AREA, workspaceId, 'conversations'] }, (old) => {
    if (!old) return old
    let row: ConversationListItem | undefined
    const pages = old.pages.map((p) => {
      const idx = p.items.findIndex((i) => i.id === convId)
      if (idx >= 0) {
        row = p.items[idx]
        return { ...p, items: p.items.filter((i) => i.id !== convId) }
      }
      return p
    })
    if (!row) return old
    bumped = true
    const isPublic = message.visibility === 'public'
    const updated: ConversationListItem = {
      ...row,
      last_message_preview: isPublic ? message.content.slice(0, 140) : row.last_message_preview,
      last_activity_at: (summary.last_activity_at as string) ?? row.last_activity_at,
      status: (summary.status as string) ?? row.status,
      priority: (summary.priority as string) ?? row.priority,
      waiting_since: (summary.waiting_since as string) ?? row.waiting_since,
      unread: message.direction === 'in' && convId !== openConversationId ? true : row.unread,
    }
    pages[0] = { ...pages[0], items: [updated, ...pages[0].items] }
    return { ...old, pages }
  })
  if (!bumped) qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
  qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'counts'] })
}

/** conversation.updated realtime handler (also reused after action mutations). */
export function applyConversationUpdated(
  qc: QueryClient,
  workspaceId: string | null,
  conv: Conversation
) {
  qc.setQueryData([AREA, workspaceId, 'conversation', conv.id], conv)
  let patched = false
  qc.setQueriesData<ListInfinite>({ queryKey: [AREA, workspaceId, 'conversations'] }, (old) => {
    if (!old) return old
    let found = false
    const pages = old.pages.map((p) => ({
      ...p,
      items: p.items.map((i) => {
        if (i.id !== conv.id) return i
        found = true
        return {
          ...i,
          status: conv.status,
          priority: conv.priority,
          subject: conv.subject,
          assignee: conv.assignee,
          tag_ids: conv.tag_ids,
          waiting_since: conv.waiting_since,
          last_activity_at: conv.last_activity_at,
        }
      }),
    }))
    if (found) patched = true
    return found ? { ...old, pages } : old
  })
  if (!patched) qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
  qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'counts'] })
}

// --- realtime hooks ---------------------------------------------------------

export function useInboxRealtime(openConversationId: string | undefined) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)

  useRealtime(
    'message.created',
    useCallback(
      (data: Record<string, unknown>) =>
        applyMessageCreated(qc, workspaceId, openConversationId, data),
      [qc, workspaceId, openConversationId]
    )
  )
  useRealtime(
    'conversation.updated',
    useCallback(
      (data: Record<string, unknown>) =>
        applyConversationUpdated(qc, workspaceId, data as unknown as Conversation),
      [qc, workspaceId]
    )
  )
  useRealtime(
    'conversation.created',
    useCallback(() => {
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'counts'] })
    }, [qc, workspaceId])
  )
  useRealtime(
    'agent_run.updated',
    useCallback(
      (data: Record<string, unknown>) => {
        const id = data.conversation_id as string | undefined
        if (id) qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversation', id] })
      },
      [qc, workspaceId]
    )
  )
  useRealtime(
    'approval.pending',
    useCallback(
      (data: Record<string, unknown>) => {
        qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'approvals'] })
        toast('Approval requested', {
          description: `${(data.agent_name as string) ?? 'An AI agent'} wants to run "${
            data.tool_key as string
          }"`,
        })
      },
      [qc, workspaceId]
    )
  )
  useRealtime(
    'approval.decided',
    useCallback(() => {
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'approvals'] })
    }, [qc, workspaceId])
  )
}

/** Whoever (contact or other member) is currently typing in this conversation. */
export function useTypingIndicator(conversationId: string | undefined): boolean {
  const [typing, setTyping] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const myId = useAuthStore((s) => s.user?.id)

  useRealtime(
    'typing',
    useCallback(
      (data: Record<string, unknown>) => {
        if (!conversationId || data.conversation_id !== conversationId) return
        if (data.source === 'member' && data.user_id === myId) return
        setTyping(Boolean(data.is_typing))
        if (timer.current) clearTimeout(timer.current)
        if (data.is_typing) timer.current = setTimeout(() => setTyping(false), 4000)
      },
      [conversationId, myId]
    )
  )
  useEffect(() => {
    setTyping(false)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [conversationId])
  return typing
}

export function usePresence(): Set<string> {
  const [online, setOnline] = useState<Set<string>>(new Set())
  useRealtime(
    'presence.state',
    useCallback((data: Record<string, unknown>) => {
      setOnline(new Set((data.online_user_ids as string[]) ?? []))
    }, [])
  )
  useRealtime(
    'presence.changed',
    useCallback((data: Record<string, unknown>) => {
      setOnline((prev) => {
        const next = new Set(prev)
        if (data.online) next.add(data.user_id as string)
        else next.delete(data.user_id as string)
        return next
      })
    }, [])
  )
  return online
}

// --- mutations --------------------------------------------------------------

export function useSendMessage(conversationId: string) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const key = [AREA, workspaceId, 'messages', conversationId]

  return useMutation({
    mutationFn: (body: {
      content: string
      visibility: 'public' | 'note'
      attachments?: AttachmentRef[]
    }) => inboxApi.sendMessage(conversationId, body),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<MsgInfinite>(key)
      const user = useAuthStore.getState().user
      const tempId = `temp-${Date.now()}`
      const optimistic: Message = {
        id: tempId,
        conversation_id: conversationId,
        direction: 'out',
        visibility: body.visibility,
        author_type: 'user',
        author_id: user?.id ?? null,
        author_name: user?.name ?? 'You',
        content: body.content,
        attachments: (body.attachments ?? []) as unknown as Message['attachments'],
        source_id: null,
        delivery_status: 'pending',
        delivery_error: null,
        meta: {},
        created_at: new Date().toISOString(),
      }
      qc.setQueryData<MsgInfinite>(key, (old) => appendToThread(old, optimistic))
      return { previous, tempId }
    },
    onError: (error, _body, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous)
      toast.error(error instanceof ApiError ? error.message : 'Failed to send')
    },
    onSuccess: (real, _body, ctx) => {
      qc.setQueryData<MsgInfinite>(key, (old) => {
        if (!old) return old
        const cleared = {
          ...old,
          pages: old.pages.map((p) => ({
            ...p,
            items: p.items.filter((m) => m.id !== ctx?.tempId),
          })),
        }
        return appendToThread(cleared, real)
      })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
    },
  })
}

export function usePatchConversation(conversationId: string) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const key = [AREA, workspaceId, 'conversation', conversationId]

  return useMutation({
    mutationFn: (patch: ConversationPatch) => inboxApi.patch(conversationId, patch),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<Conversation>(key)
      if (previous) {
        qc.setQueryData<Conversation>(key, { ...previous, ...stripUnset(patch) })
      }
      return { previous }
    },
    onError: (error, _patch, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous)
      toast.error(error instanceof ApiError ? error.message : 'Update failed')
    },
    onSuccess: (conv) => applyConversationUpdated(qc, workspaceId, conv),
  })
}

function stripUnset(patch: ConversationPatch): Partial<Conversation> {
  const out: Record<string, unknown> = {}
  if (patch.status !== undefined) out.status = patch.status
  if (patch.priority !== undefined) out.priority = patch.priority
  if (patch.snoozed_until !== undefined) out.snoozed_until = patch.snoozed_until
  return out as Partial<Conversation>
}

export function useMarkRead(conversationId: string | undefined) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: () => inboxApi.markRead(conversationId!),
    onSuccess: () => {
      qc.setQueriesData<ListInfinite>({ queryKey: [AREA, workspaceId, 'conversations'] }, (old) => {
        if (!old) return old
        return {
          ...old,
          pages: old.pages.map((p) => ({
            ...p,
            items: p.items.map((i) => (i.id === conversationId ? { ...i, unread: false } : i)),
          })),
        }
      })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'counts'] })
    },
  })
}

export function useConversationTags(conversationId: string) {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  const apply = (conv: Conversation) => applyConversationUpdated(qc, workspaceId, conv)
  return {
    add: useMutation({
      mutationFn: (tagId: string) => inboxApi.addTag(conversationId, tagId),
      onSuccess: apply,
      onError: () => toast.error(t('inbox.failed_to_add_tag')),
    }),
    remove: useMutation({
      mutationFn: (tagId: string) => inboxApi.removeTag(conversationId, tagId),
      onSuccess: apply,
      onError: () => toast.error(t('inbox.failed_to_remove_tag')),
    }),
  }
}

export function useCreateConversation() {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: (body: {
      contact_id: string
      inbox_id: string
      content: string
      subject?: string
    }) => inboxApi.create(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'conversations'] })
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'counts'] })
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not start conversation'),
  })
}

export function useCopilot() {
  return useMutation({
    mutationFn: (conversationId: string) => copilotSuggest(conversationId),
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : 'Copilot is unavailable'),
  })
}

export function useDecideApproval() {
  const qc = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: ({ id, approved, note }: { id: string; approved: boolean; note?: string }) =>
      approvalsApi.decide(id, approved, note),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: [AREA, workspaceId, 'approvals'] })
      toast.success(vars.approved ? 'Approved' : 'Rejected')
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : 'Decision failed'),
  })
}

// ---------------------------------------------------------------------------
// Saved views + filter DSL (docs/CHATWOOT-BACKLOG.md §1.3)
// ---------------------------------------------------------------------------

export function useFilterCatalog() {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'filter-catalog'],
    enabled: !!workspaceId,
    queryFn: viewsApi.catalog,
    // The catalog only changes when someone edits an attribute definition.
    staleTime: 5 * 60_000,
  })
}

export function useSavedViews(kind = 'conversation') {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'views', kind],
    enabled: !!workspaceId,
    queryFn: () => viewsApi.list(kind),
  })
}

function useViewsInvalidator() {
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useCallback(
    () => queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, 'views'] }),
    [queryClient, workspaceId]
  )
}

export function useCreateView() {
  const invalidate = useViewsInvalidator()
  return useMutation({
    mutationFn: viewsApi.create,
    onSuccess: async (view) => {
      await invalidate()
      toast.success(`View “${view.name}” saved`)
    },
    onError: (error) => toast.error(errorMessage(error, 'Could not save the view')),
  })
}

export function useUpdateView() {
  const invalidate = useViewsInvalidator()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof viewsApi.update>[1] }) =>
      viewsApi.update(id, body),
    onSuccess: invalidate,
    onError: (error) => toast.error(errorMessage(error, 'Could not update the view')),
  })
}

export function useDeleteView() {
  const invalidate = useViewsInvalidator()
  return useMutation({
    mutationFn: viewsApi.remove,
    onSuccess: async () => {
      await invalidate()
      toast.success(t('inbox.view_deleted'))
    },
    onError: (error) => toast.error(errorMessage(error, 'Could not delete the view')),
  })
}

/** Ad-hoc filter run — the builder's live preview and report drill-down. */
export function useFilterPreview(query: FilterQuery, enabled: boolean) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'filter-preview', query],
    enabled: enabled && !!workspaceId,
    queryFn: () => viewsApi.search(query, undefined, 25),
    retry: false,
  })
}

// ---------------------------------------------------------------------------
// Bulk actions (§1.4)
// ---------------------------------------------------------------------------

export function useBulkAction() {
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: bulkApi.run,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: [AREA, workspaceId] })
      if (result.failed > 0) {
        toast.warning(`${result.succeeded} updated, ${result.failed} failed`, {
          description: result.errors?.[0]?.error,
        })
      } else {
        toast.success(
          `${result.succeeded} conversation${result.succeeded === 1 ? '' : 's'} updated`
        )
      }
    },
    onError: (error) => toast.error(errorMessage(error, 'Bulk action failed')),
  })
}

// ---------------------------------------------------------------------------
// Participants + mentions (§1.2)
// ---------------------------------------------------------------------------

export function useParticipants(conversationId: string | null) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'participants', conversationId],
    enabled: !!workspaceId && !!conversationId,
    queryFn: () => collaborationApi.listParticipants(conversationId as string),
  })
}

export function useToggleParticipant(conversationId: string) {
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: async ({ userId, join }: { userId: string; join: boolean }) => {
      if (join) await collaborationApi.addParticipant(conversationId, userId)
      else await collaborationApi.removeParticipant(conversationId, userId)
    },
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: [AREA, workspaceId, 'participants', conversationId],
      }),
    onError: (error) => toast.error(errorMessage(error, 'Could not update watchers')),
  })
}

export function useMentions(unreadOnly = false) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'mentions', unreadOnly],
    enabled: !!workspaceId,
    queryFn: () => collaborationApi.listMentions(unreadOnly),
  })
}

export function useMarkMentionsRead() {
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: (conversationId?: string | null) =>
      collaborationApi.markMentionsRead(conversationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, 'mentions'] }),
  })
}

// ---------------------------------------------------------------------------
// Working hours (§1.1)
// ---------------------------------------------------------------------------

export function useWorkingHours(inboxId: string | null) {
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useQuery({
    queryKey: [AREA, workspaceId, 'working-hours', inboxId],
    enabled: !!workspaceId && !!inboxId,
    queryFn: () => workingHoursApi.get(inboxId as string),
  })
}

export function useSaveWorkingHours(inboxId: string) {
  const queryClient = useQueryClient()
  const workspaceId = useAuthStore((s) => s.workspaceId)
  return useMutation({
    mutationFn: (body: Parameters<typeof workingHoursApi.set>[1]) =>
      workingHoursApi.set(inboxId, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: [AREA, workspaceId, 'working-hours', inboxId],
      })
      toast.success(t('inbox.working_hours_saved'))
    },
    onError: (error) => toast.error(errorMessage(error, 'Could not save working hours')),
  })
}
