import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import { assignLocation } from './components/integrations/redirect'
import {
  apiKeysApi,
  attributesApi,
  auditApi,
  channelsApi,
  integrationsApi,
  integrationsKeys,
  membersApi,
  profileApi,
  rolesApi,
  slasApi,
  workspaceApi,
  type ApiKeyCreate,
  type CredentialIn,
  type CustomAttributeUpdate,
  type InboxCreate,
  type InboxUpdate,
  type InvitationCreate,
  type MemberUpdate,
  type RoleCreate,
  type RoleUpdate,
  type SlaPolicyCreate,
  type SlaPolicyUpdate,
  type WorkspaceUpdate,
} from './api'
import { t } from '@/i18n'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function useKey(...parts: (string | number)[]) {
  return ['settings', currentWorkspaceId(), ...parts]
}

// --- workspace --------------------------------------------------------------

export function useWorkspace() {
  return useQuery({ queryKey: useKey('workspace'), queryFn: workspaceApi.get })
}

export function useUpdateWorkspace() {
  const queryClient = useQueryClient()
  const key = useKey('workspace')
  return useMutation({
    mutationFn: (body: WorkspaceUpdate) => workspaceApi.update(body),
    onSuccess: () => {
      toast.success(t('settings.workspace_updated'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not update workspace')),
  })
}

// --- members + invitations --------------------------------------------------

export function useMembers() {
  return useQuery({ queryKey: useKey('members'), queryFn: membersApi.list })
}

export function useInvitations() {
  return useQuery({ queryKey: useKey('invitations'), queryFn: membersApi.listInvitations })
}

export function useUpdateMember() {
  const queryClient = useQueryClient()
  const key = useKey('members')
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MemberUpdate }) => membersApi.update(id, body),
    onSuccess: () => {
      toast.success(t('settings.member_updated'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not update member')),
  })
}

export function useRemoveMember() {
  const queryClient = useQueryClient()
  const key = useKey('members')
  return useMutation({
    mutationFn: (id: string) => membersApi.remove(id),
    onSuccess: () => {
      toast.success(t('settings.member_removed'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not remove member')),
  })
}

export function useInvite() {
  const queryClient = useQueryClient()
  const key = useKey('invitations')
  return useMutation({
    mutationFn: (body: InvitationCreate) => membersApi.invite(body),
    onSuccess: () => {
      toast.success(t('settings.invitation_sent'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not send invite')),
  })
}

export function useRevokeInvitation() {
  const queryClient = useQueryClient()
  const key = useKey('invitations')
  return useMutation({
    mutationFn: (id: string) => membersApi.revokeInvitation(id),
    onSuccess: () => {
      toast.success(t('settings.invitation_revoked'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not revoke invite')),
  })
}

// --- roles ------------------------------------------------------------------

export function useRoles() {
  return useQuery({ queryKey: useKey('roles'), queryFn: rolesApi.list })
}

export function usePermissionCatalog() {
  return useQuery({ queryKey: useKey('roles', 'catalog'), queryFn: rolesApi.catalog })
}

export function useCreateRole() {
  const queryClient = useQueryClient()
  const key = useKey('roles')
  return useMutation({
    mutationFn: (body: RoleCreate) => rolesApi.create(body),
    onSuccess: () => {
      toast.success(t('settings.role_created'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create role')),
  })
}

export function useUpdateRole() {
  const queryClient = useQueryClient()
  const key = useKey('roles')
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: RoleUpdate }) => rolesApi.update(id, body),
    onSuccess: () => {
      toast.success(t('settings.role_saved'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save role')),
  })
}

export function useDeleteRole() {
  const queryClient = useQueryClient()
  const key = useKey('roles')
  return useMutation({
    mutationFn: (id: string) => rolesApi.remove(id),
    onSuccess: () => {
      toast.success(t('settings.role_deleted'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete role')),
  })
}

// --- api keys ---------------------------------------------------------------

export function useApiKeys() {
  return useQuery({ queryKey: useKey('api-keys'), queryFn: apiKeysApi.list })
}

export function useCreateApiKey() {
  const queryClient = useQueryClient()
  const key = useKey('api-keys')
  return useMutation({
    mutationFn: (body: ApiKeyCreate) => apiKeysApi.create(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key }),
    onError: (error) => toast.error(errMessage(error, 'Could not create key')),
  })
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient()
  const key = useKey('api-keys')
  return useMutation({
    mutationFn: (id: string) => apiKeysApi.revoke(id),
    onSuccess: () => {
      toast.success(t('settings.key_revoked'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not revoke key')),
  })
}

// --- audit ------------------------------------------------------------------

export function useAuditLog(params: { action?: string; actor_id?: string }) {
  return useQuery({
    queryKey: useKey('audit', params.action ?? '', params.actor_id ?? ''),
    queryFn: () => auditApi.list({ ...params, limit: 100 }),
  })
}

// --- channels ---------------------------------------------------------------

export function useInboxes() {
  return useQuery({ queryKey: useKey('inboxes'), queryFn: channelsApi.list })
}

export function useCreateInbox() {
  const queryClient = useQueryClient()
  const key = useKey('inboxes')
  return useMutation({
    mutationFn: (body: InboxCreate) => channelsApi.create(body),
    onSuccess: () => {
      toast.success(t('settings.channel_created'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create channel')),
  })
}

export function useUpdateInbox() {
  const queryClient = useQueryClient()
  const key = useKey('inboxes')
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: InboxUpdate }) => channelsApi.update(id, body),
    onSuccess: () => {
      toast.success(t('settings.channel_updated'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not update channel')),
  })
}

export function useDeleteInbox() {
  const queryClient = useQueryClient()
  const key = useKey('inboxes')
  return useMutation({
    mutationFn: (id: string) => channelsApi.remove(id),
    onSuccess: () => {
      toast.success(t('settings.channel_deleted'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete channel')),
  })
}

// --- integrations (docs/INTEGRATIONS-CONTRACTS.md) ---------------------------

export function useIntegrations() {
  return useQuery({
    queryKey: integrationsKeys.all(currentWorkspaceId()),
    queryFn: integrationsApi.list,
  })
}

/** POST connect → hand the browser to the provider's authorize URL. */
export function useConnectIntegration() {
  return useMutation({
    mutationFn: ({ provider, returnTo }: { provider: string; returnTo?: string }) =>
      integrationsApi.connect(provider, returnTo),
    onSuccess: (data) => assignLocation(data.authorize_url),
    onError: (error) => toast.error(errMessage(error, 'Could not start the connection')),
  })
}

/** Reauthorize an existing connection in place (same OAuth round trip). */
export function useReconnectConnection() {
  return useMutation({
    mutationFn: ({ connectionId, returnTo }: { connectionId: string; returnTo?: string }) =>
      integrationsApi.reconnect(connectionId, returnTo),
    onSuccess: (data) => assignLocation(data.authorize_url),
    onError: (error) => toast.error(errMessage(error, 'Could not start the reconnection')),
  })
}

export function useDisconnectConnection() {
  const queryClient = useQueryClient()
  const key = integrationsKeys.all(currentWorkspaceId())
  return useMutation({
    mutationFn: (connectionId: string) => integrationsApi.disconnect(connectionId),
    onSuccess: () => {
      toast.success(t('settings.disconnected'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not disconnect')),
  })
}

export function usePutCredentials() {
  const queryClient = useQueryClient()
  const key = integrationsKeys.all(currentWorkspaceId())
  return useMutation({
    mutationFn: ({ provider, body }: { provider: string; body: CredentialIn }) =>
      integrationsApi.putCredentials(provider, body),
    onSuccess: () => {
      toast.success(t('settings.app_credentials_saved'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save credentials')),
  })
}

export function useDeleteCredentials() {
  const queryClient = useQueryClient()
  const key = integrationsKeys.all(currentWorkspaceId())
  return useMutation({
    mutationFn: (provider: string) => integrationsApi.deleteCredentials(provider),
    onSuccess: () => {
      toast.success(t('settings.workspace_credentials_removed'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not remove credentials')),
  })
}

// --- sla policies -----------------------------------------------------------

export function useSlaPolicies() {
  return useQuery({ queryKey: useKey('slas'), queryFn: slasApi.list })
}

export function useCreateSlaPolicy() {
  const queryClient = useQueryClient()
  const key = useKey('slas')
  return useMutation({
    mutationFn: (body: SlaPolicyCreate) => slasApi.create(body),
    onSuccess: () => {
      toast.success(t('settings.sla_policy_created'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create SLA policy')),
  })
}

export function useUpdateSlaPolicy() {
  const queryClient = useQueryClient()
  const key = useKey('slas')
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SlaPolicyUpdate }) => slasApi.update(id, body),
    onSuccess: () => {
      toast.success(t('settings.sla_policy_saved'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save SLA policy')),
  })
}

export function useDeleteSlaPolicy() {
  const queryClient = useQueryClient()
  const key = useKey('slas')
  return useMutation({
    mutationFn: (id: string) => slasApi.remove(id),
    onSuccess: () => {
      toast.success(t('settings.sla_policy_deleted'))
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete SLA policy')),
  })
}

// --- profile ----------------------------------------------------------------

export function useUpdateProfile() {
  return useMutation({
    mutationFn: (body: { name?: string; avatar_url?: string; locale?: string }) =>
      profileApi.update(body),
    onSuccess: () => toast.success(t('settings.profile_updated')),
    onError: (error) => toast.error(errMessage(error, 'Could not update profile')),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      profileApi.changePassword(body),
    onSuccess: () => toast.success(t('settings.password_changed')),
    onError: (error) => toast.error(errMessage(error, 'Could not change password')),
  })
}

// --- custom attribute definitions -------------------------------------------

export function useCustomAttributes(attributeModel?: string) {
  return useQuery({
    queryKey: useKey('custom-attributes', attributeModel ?? 'all'),
    queryFn: () => attributesApi.list(attributeModel),
  })
}

function useAttributesInvalidator() {
  const queryClient = useQueryClient()
  return () => {
    void queryClient.invalidateQueries({
      queryKey: ['settings', currentWorkspaceId(), 'custom-attributes'],
    })
    // The inbox filter builder renders from the same definitions.
    void queryClient.invalidateQueries({
      queryKey: ['inbox', currentWorkspaceId(), 'filter-catalog'],
    })
  }
}

export function useCreateAttribute() {
  const invalidate = useAttributesInvalidator()
  return useMutation({
    mutationFn: attributesApi.create,
    onSuccess: () => {
      invalidate()
      toast.success(t('settings.attribute_created'))
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create the attribute')),
  })
}

export function useUpdateAttribute() {
  const invalidate = useAttributesInvalidator()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CustomAttributeUpdate }) =>
      attributesApi.update(id, body),
    onSuccess: () => {
      invalidate()
      toast.success(t('settings.attribute_updated'))
    },
    onError: (error) => toast.error(errMessage(error, 'Could not update the attribute')),
  })
}

export function useDeleteAttribute() {
  const invalidate = useAttributesInvalidator()
  return useMutation({
    mutationFn: attributesApi.remove,
    onSuccess: () => {
      invalidate()
      toast.success(t('settings.attribute_deleted_stored_values_are_untouched'))
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete the attribute')),
  })
}
