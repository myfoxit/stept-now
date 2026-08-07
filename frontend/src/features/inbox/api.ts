/**
 * Inbox API — conversations, messages, inboxes, canned responses, members,
 * teams, tags, file uploads, copilot, and approval decisions.
 */

import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'
import { useAuthStore } from '@/stores/auth'
import { qs, type CursorPage } from '@/features/contacts/api'

export type Conversation = components['schemas']['ConversationOut']
export type ConversationListItem = components['schemas']['ConversationListItem']
export type Message = components['schemas']['MessageOut']
export type Counts = components['schemas']['ConversationCounts']
export type Inbox = components['schemas']['InboxOut']
export type Canned = components['schemas']['CannedResponseOut']
export type Approval = components['schemas']['ApprovalOut']
export type CopilotResult = components['schemas']['CopilotResult']
export type FileOut = components['schemas']['FileOut']
export type Member = components['schemas']['MembershipOut']
export type Team = components['schemas']['TeamOut']
export type ConversationSla = components['schemas']['ConversationSlaOut']
export type SlaPolicy = components['schemas']['SlaPolicyOut']
export type SlaEvent = components['schemas']['SlaEventOut']
export type MessageFeedback = components['schemas']['MessageFeedbackOut']
export type { Tag } from '@/features/contacts/api'

export interface AttachmentRef {
  key: string
  name: string
  size: number
  content_type: string
}

/** Citation footnote embedded in agent/AI message meta. */
export interface Citation {
  n: number
  title: string
  url?: string | null
  document_id?: string | null
}

export type ConversationStatus = 'open' | 'pending' | 'snoozed' | 'resolved'
export type Priority = 'none' | 'low' | 'medium' | 'high' | 'urgent'

export interface ConversationFilters {
  status?: string[]
  inbox_id?: string
  assignee?: string
  team_id?: string
  contact_id?: string
  tag_id?: string
  priority?: string
  q?: string
  /** Layers a saved view's filter document on top of the params above. */
  view_id?: string
}

export interface ConversationPatch {
  status?: ConversationStatus
  snoozed_until?: string | null
  priority?: Priority
  assignee_user_id?: string | null
  team_id?: string | null
}

export const inboxApi = {
  listConversations: (filters: ConversationFilters, cursor?: string) =>
    api.get<CursorPage<ConversationListItem>>(
      ws('/conversations') + qs({ ...filters, cursor })
    ),
  counts: () => api.get<Counts>(ws('/conversations/counts')),
  get: (id: string) => api.get<Conversation>(ws(`/conversations/${id}`)),
  create: (body: { contact_id: string; inbox_id: string; content: string; subject?: string }) =>
    api.post<Conversation>(ws('/conversations'), body),
  patch: (id: string, body: ConversationPatch) =>
    api.patch<Conversation>(ws(`/conversations/${id}`), body),
  listMessages: (id: string, cursor?: string) =>
    api.get<CursorPage<Message>>(ws(`/conversations/${id}/messages`) + qs({ cursor })),
  sendMessage: (
    id: string,
    body: { content: string; visibility: 'public' | 'note'; attachments?: AttachmentRef[] }
  ) => api.post<Message>(ws(`/conversations/${id}/messages`), body),
  addTag: (id: string, tagId: string) =>
    api.post<Conversation>(ws(`/conversations/${id}/tags`), { tag_id: tagId }),
  removeTag: (id: string, tagId: string) =>
    api.delete<Conversation>(ws(`/conversations/${id}/tags/${tagId}`)),
  markRead: (id: string) => api.post<{ message: string }>(ws(`/conversations/${id}/read`)),
}

export const listInboxes = () => api.get<Inbox[]>(ws('/inboxes'))
export const listCanned = () => api.get<Canned[]>(ws('/canned-responses'))
export const listMembers = () => api.get<Member[]>(ws('/members'))
export const listTeams = () => api.get<Team[]>(ws('/teams'))

export const uploadFile = (file: File) => api.upload<FileOut>(ws('/files'), file)

export const copilotSuggest = (conversationId: string) =>
  api.post<CopilotResult>(ws('/ai/copilot/suggest'), { conversation_id: conversationId })

export const approvalsApi = {
  listPending: () => api.get<Approval[]>(ws('/ai/approvals') + qs({ status: 'pending' })),
  decide: (id: string, approved: boolean, note?: string) =>
    api.post<Approval>(ws(`/ai/approvals/${id}/decide`), { approved, note }),
}

export const slaApi = {
  get: (conversationId: string) =>
    api.get<ConversationSla>(ws(`/conversations/${conversationId}/sla`)),
  apply: (conversationId: string, slaPolicyId: string | null) =>
    api.put<ConversationSla>(ws(`/conversations/${conversationId}/sla`), {
      sla_policy_id: slaPolicyId,
    }),
  listPolicies: () => api.get<SlaPolicy[]>(ws('/slas')),
}

export const feedbackApi = {
  list: (conversationId: string, messageId: string) =>
    api.get<MessageFeedback[]>(
      ws(`/conversations/${conversationId}/messages/${messageId}/feedback`)
    ),
  submit: (conversationId: string, messageId: string, rating: 'up' | 'down') =>
    api.post<MessageFeedback>(
      ws(`/conversations/${conversationId}/messages/${messageId}/feedback`),
      { rating }
    ),
}

/** Read citations out of a message's meta (agent/AI replies). */
export function messageCitations(message: Pick<Message, 'meta'>): Citation[] {
  const raw = (message.meta as Record<string, unknown> | undefined)?.citations
  return Array.isArray(raw) ? (raw as Citation[]) : []
}

/** Build the workspace-scoped serving URL for a stored attachment key. */
export function fileUrl(key: string): string {
  const id = useAuthStore.getState().workspaceId
  return id ? `/api/v1/w/${id}/files/${key}` : `#${key}`
}

// ---------------------------------------------------------------------------
// Saved views + filter DSL (docs/CHATWOOT-BACKLOG.md §1.3)
// ---------------------------------------------------------------------------

export type SavedView = components['schemas']['SavedViewOut']
export type FilterCatalog = components['schemas']['FilterCatalogOut']
export type FilterField = components['schemas']['FilterFieldOut']

export interface FilterCondition {
  field: string
  op: string
  value?: unknown
}

export interface FilterQuery {
  match: 'all' | 'any'
  conditions: FilterCondition[]
}

export const EMPTY_QUERY: FilterQuery = { match: 'all', conditions: [] }

export const viewsApi = {
  catalog: () => api.get<FilterCatalog>(ws('/views/catalog')),
  list: (kind = 'conversation') => api.get<SavedView[]>(ws('/views') + qs({ kind })),
  create: (body: {
    name: string
    kind?: string
    visibility?: 'personal' | 'shared'
    query: FilterQuery
    icon?: string | null
  }) => api.post<SavedView>(ws('/views'), body),
  update: (
    id: string,
    body: Partial<{ name: string; visibility: string; query: FilterQuery; icon: string | null }>
  ) => api.patch<SavedView>(ws(`/views/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/views/${id}`)),
  /** Ad-hoc filter run — powers the builder preview and report drill-down. */
  search: (query: FilterQuery, cursor?: string, limit?: number) =>
    api.post<CursorPage<ConversationListItem>>(
      ws('/conversations/search') + qs({ cursor, limit }),
      query
    ),
}

// ---------------------------------------------------------------------------
// Bulk actions (§1.4)
// ---------------------------------------------------------------------------

export type BulkAction =
  'set_status' | 'set_priority' | 'assign_user' | 'assign_team' | 'add_tag' | 'remove_tag'

export type BulkResult = components['schemas']['BulkActionResult']

export const bulkApi = {
  run: (body: {
    action: BulkAction
    params?: Record<string, unknown>
    conversation_ids?: string[]
    query?: FilterQuery
  }) => api.post<BulkResult>(ws('/conversations/bulk'), body),
}

// ---------------------------------------------------------------------------
// Participants + mentions (§1.2)
// ---------------------------------------------------------------------------

export type Participant = components['schemas']['ParticipantOut']
export type Mention = components['schemas']['MentionOut']

export const collaborationApi = {
  listParticipants: (conversationId: string) =>
    api.get<Participant[]>(ws(`/conversations/${conversationId}/participants`)),
  addParticipant: (conversationId: string, userId: string) =>
    api.post<Participant[]>(ws(`/conversations/${conversationId}/participants`), {
      user_id: userId,
    }),
  removeParticipant: (conversationId: string, userId: string) =>
    api.delete<{ message: string }>(ws(`/conversations/${conversationId}/participants/${userId}`)),
  listMentions: (unreadOnly = false) =>
    api.get<Mention[]>(ws('/mentions') + qs({ unread_only: unreadOnly })),
  markMentionsRead: (conversationId?: string | null) =>
    api.post<{ marked: number }>(ws('/mentions/read'), {
      conversation_id: conversationId ?? null,
    }),
}

// ---------------------------------------------------------------------------
// Working hours (§1.1)
// ---------------------------------------------------------------------------

export type WorkingHours = components['schemas']['WorkingHoursOut']
export type WorkingHourDay = components['schemas']['WorkingHourOut']

export const workingHoursApi = {
  get: (inboxId: string) => api.get<WorkingHours>(ws(`/inboxes/${inboxId}/working-hours`)),
  set: (
    inboxId: string,
    body: {
      days: Array<{
        day_of_week: number
        closed_all_day?: boolean
        open_all_day?: boolean
        open_minute?: number
        close_minute?: number
      }>
      enabled?: boolean
      timezone?: string
      out_of_office_message?: string | null
    }
  ) => api.put<WorkingHours>(ws(`/inboxes/${inboxId}/working-hours`), body),
}
