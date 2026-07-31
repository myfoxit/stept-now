import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import {
  apiKeysApi,
  auditApi,
  channelsApi,
  membersApi,
  profileApi,
  rolesApi,
  slasApi,
  workspaceApi,
  type ApiKeyCreate,
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
      toast.success('Workspace updated')
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
      toast.success('Member updated')
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
      toast.success('Member removed')
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
      toast.success('Invitation sent')
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
      toast.success('Invitation revoked')
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
      toast.success('Role created')
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
      toast.success('Role saved')
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
      toast.success('Role deleted')
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
      toast.success('Key revoked')
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
      toast.success('Channel created')
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
      toast.success('Channel updated')
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
      toast.success('Channel deleted')
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete channel')),
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
      toast.success('SLA policy created')
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
      toast.success('SLA policy saved')
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
      toast.success('SLA policy deleted')
      void queryClient.invalidateQueries({ queryKey: key })
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete SLA policy')),
  })
}

// --- profile ----------------------------------------------------------------

export function useUpdateProfile() {
  return useMutation({
    mutationFn: (body: { name?: string; avatar_url?: string }) => profileApi.update(body),
    onSuccess: () => toast.success('Profile updated'),
    onError: (error) => toast.error(errMessage(error, 'Could not update profile')),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      profileApi.changePassword(body),
    onSuccess: () => toast.success('Password changed'),
    onError: (error) => toast.error(errMessage(error, 'Could not change password')),
  })
}
