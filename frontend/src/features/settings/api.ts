import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Workspace = components['schemas']['WorkspaceOut']
export type WorkspaceUpdate = components['schemas']['WorkspaceUpdate']
export type Membership = components['schemas']['MembershipOut']
export type MemberUpdate = components['schemas']['MemberUpdate']
export type Invitation = components['schemas']['InvitationOut']
export type InvitationCreate = components['schemas']['InvitationCreate']
export type Role = components['schemas']['RoleOut']
export type RoleCreate = components['schemas']['RoleCreate']
export type RoleUpdate = components['schemas']['RoleUpdate']
export type PermissionCatalog = components['schemas']['PermissionCatalogOut']
/**
 * API keys grew a nullable `agent_id` for the MCP channel (a set agent_id binds
 * the key to that agent's /mcp/agents/{id} endpoint only). The generated
 * schema.d.ts predates the field — extend locally until `make types` runs.
 */
export type ApiKey = components['schemas']['ApiKeyOut'] & { agent_id?: string | null }
export type ApiKeyCreated = components['schemas']['ApiKeyCreated'] & { agent_id?: string | null }
export type ApiKeyCreate = components['schemas']['ApiKeyCreate'] & { agent_id?: string | null }
export type AuditLog = components['schemas']['AuditLogOut']
export type Inbox = components['schemas']['InboxOut']
export type InboxCreate = components['schemas']['InboxCreate']
export type InboxUpdate = components['schemas']['InboxUpdate']
export type User = components['schemas']['UserOut']
export type SlaPolicy = components['schemas']['SlaPolicyOut']
export type SlaPolicyCreate = components['schemas']['SlaPolicyCreate']
export type SlaPolicyUpdate = components['schemas']['SlaPolicyUpdate']

interface OffsetPage<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export const workspaceApi = {
  get: () => api.get<Workspace>(ws('')),
  update: (body: WorkspaceUpdate) => api.patch<Workspace>(ws(''), body),
}

export const membersApi = {
  list: () => api.get<Membership[]>(ws('/members')),
  update: (id: string, body: MemberUpdate) => api.patch<Membership>(ws(`/members/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/members/${id}`)),
  listInvitations: () => api.get<Invitation[]>(ws('/invitations')),
  invite: (body: InvitationCreate) => api.post<Invitation>(ws('/invitations'), body),
  revokeInvitation: (id: string) => api.delete<{ message: string }>(ws(`/invitations/${id}`)),
}

export const rolesApi = {
  list: () => api.get<Role[]>(ws('/roles')),
  catalog: () => api.get<PermissionCatalog>(ws('/roles/catalog')),
  create: (body: RoleCreate) => api.post<Role>(ws('/roles'), body),
  update: (id: string, body: RoleUpdate) => api.patch<Role>(ws(`/roles/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/roles/${id}`)),
}

export const apiKeysApi = {
  list: () => api.get<ApiKey[]>(ws('/api-keys')),
  create: (body: ApiKeyCreate) => api.post<ApiKeyCreated>(ws('/api-keys'), body),
  revoke: (id: string) => api.delete<ApiKey>(ws(`/api-keys/${id}`)),
}

export const auditApi = {
  list: (params: { action?: string; actor_id?: string; limit?: number; offset?: number }) =>
    api.get<OffsetPage<AuditLog>>(ws('/audit'), { query: params }),
}

export const channelsApi = {
  list: () => api.get<Inbox[]>(ws('/inboxes')),
  create: (body: InboxCreate) => api.post<Inbox>(ws('/inboxes'), body),
  update: (id: string, body: InboxUpdate) => api.patch<Inbox>(ws(`/inboxes/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/inboxes/${id}`)),
}

export const slasApi = {
  list: () => api.get<SlaPolicy[]>(ws('/slas')),
  create: (body: SlaPolicyCreate) => api.post<SlaPolicy>(ws('/slas'), body),
  update: (id: string, body: SlaPolicyUpdate) => api.patch<SlaPolicy>(ws(`/slas/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/slas/${id}`)),
}

export const profileApi = {
  update: (body: { name?: string; avatar_url?: string }) => api.patch<User>('/api/v1/me', body),
  changePassword: (body: { current_password: string; new_password: string }) =>
    api.post<{ message: string }>('/api/v1/me/change-password', body),
}

// ---------------------------------------------------------------------------
// Custom attribute definitions (docs/CHATWOOT-BACKLOG.md §1.6)
// ---------------------------------------------------------------------------

export type CustomAttribute = components['schemas']['CustomAttributeOut']
export type CustomAttributeCreate = components['schemas']['CustomAttributeCreate']
export type CustomAttributeUpdate = components['schemas']['CustomAttributeUpdate']

export const attributesApi = {
  list: (attributeModel?: string) =>
    api.get<CustomAttribute[]>(ws('/custom-attributes'), {
      query: attributeModel ? { attribute_model: attributeModel } : {},
    }),
  create: (body: CustomAttributeCreate) =>
    api.post<CustomAttribute>(ws('/custom-attributes'), body),
  update: (id: string, body: CustomAttributeUpdate) =>
    api.patch<CustomAttribute>(ws(`/custom-attributes/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/custom-attributes/${id}`)),
}
