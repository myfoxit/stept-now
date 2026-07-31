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
  api.post<CopilotResult>('/api/v1/ai/copilot/suggest', { conversation_id: conversationId })

export const approvalsApi = {
  listPending: () => api.get<Approval[]>(ws('/ai/approvals') + qs({ status: 'pending' })),
  decide: (id: string, approved: boolean, note?: string) =>
    api.post<Approval>(ws(`/ai/approvals/${id}/decide`), { approved, note }),
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
