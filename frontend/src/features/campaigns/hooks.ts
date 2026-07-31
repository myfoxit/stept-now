import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import {
  campaignsApi,
  listInboxes,
  listMembers,
  listSegments,
  listTags,
  type CampaignCreate,
  type CampaignUpdate,
} from './api'

const AREA = 'campaigns'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

// --- queries ----------------------------------------------------------------

export function useCampaigns(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId], queryFn: campaignsApi.list, enabled })
}

export function useCampaignInboxes(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId, 'inboxes'], queryFn: listInboxes, enabled })
}

export function useCampaignSegments(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId, 'segments'], queryFn: listSegments, enabled })
}

export function useCampaignTags(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId, 'tags'], queryFn: listTags, enabled })
}

export function useCampaignMembers(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId, 'members'], queryFn: listMembers, enabled })
}

// --- mutations --------------------------------------------------------------

/** Invalidate exactly the campaigns list — supporting lookups stay cached. */
function useCampaignsInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return () => queryClient.invalidateQueries({ queryKey: [AREA, workspaceId], exact: true })
}

export function useCreateCampaign() {
  const invalidate = useCampaignsInvalidate()
  return useMutation({
    mutationFn: (body: CampaignCreate) => campaignsApi.create(body),
    onSuccess: () => {
      toast.success('Campaign created')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create campaign')),
  })
}

export function useUpdateCampaign() {
  const invalidate = useCampaignsInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CampaignUpdate }) =>
      campaignsApi.update(id, body),
    onSuccess: () => {
      toast.success('Campaign saved')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save campaign')),
  })
}

export function useDeleteCampaign() {
  const invalidate = useCampaignsInvalidate()
  return useMutation({
    mutationFn: (id: string) => campaignsApi.remove(id),
    onSuccess: () => {
      toast.success('Campaign deleted')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete campaign')),
  })
}

export function useActivateCampaign() {
  const invalidate = useCampaignsInvalidate()
  return useMutation({
    mutationFn: (id: string) => campaignsApi.activate(id),
    onSuccess: () => {
      toast.success('Campaign activated')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not activate campaign')),
  })
}

export function usePauseCampaign() {
  const invalidate = useCampaignsInvalidate()
  return useMutation({
    mutationFn: (id: string) => campaignsApi.pause(id),
    onSuccess: () => {
      toast.success('Campaign paused')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not pause campaign')),
  })
}
