import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import { checklistsApi, listTourOptions, type ChecklistCreate, type ChecklistUpdate } from './api'
import { t } from '@/i18n'

const AREA = 'checklists'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** Publishing an empty checklist is a 409 — say what to do about it. */
function publishError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 409) {
    return 'Add at least one item before publishing'
  }
  return errMessage(error, fallback)
}

// --- queries ----------------------------------------------------------------

export function useChecklists(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId], queryFn: checklistsApi.list, enabled })
}

export function useChecklist(id: string | undefined) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id],
    queryFn: () => checklistsApi.get(id!),
    enabled: Boolean(id),
  })
}

export function useChecklistStats(id: string | undefined, enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id, 'stats'],
    queryFn: () => checklistsApi.stats(id!),
    enabled: Boolean(id) && enabled,
  })
}

/** Tour picker options for item actions/completions (local, not `features/tours`). */
export function useTourOptions(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId, 'tours'], queryFn: listTourOptions, enabled })
}

// --- mutations --------------------------------------------------------------

/** Invalidate the list plus, when given, exactly one checklist's detail + stats. */
function useChecklistInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId], exact: true })
    if (id) void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, id] })
  }
}

export function useCreateChecklist() {
  const invalidate = useChecklistInvalidate()
  return useMutation({
    mutationFn: (body: ChecklistCreate) => checklistsApi.create(body),
    onSuccess: () => {
      toast.success(t('checklists.checklist_created'))
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create checklist')),
  })
}

export function useUpdateChecklist() {
  const invalidate = useChecklistInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ChecklistUpdate }) =>
      checklistsApi.update(id, body),
    onSuccess: (checklist) => {
      toast.success(t('checklists.checklist_saved'))
      invalidate(checklist.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save checklist')),
  })
}

export function useDeleteChecklist() {
  const invalidate = useChecklistInvalidate()
  return useMutation({
    mutationFn: (id: string) => checklistsApi.remove(id),
    onSuccess: () => {
      toast.success(t('checklists.checklist_deleted'))
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete checklist')),
  })
}

export function usePublishChecklist() {
  const invalidate = useChecklistInvalidate()
  return useMutation({
    mutationFn: (id: string) => checklistsApi.publish(id),
    onSuccess: (checklist) => {
      toast.success(t('checklists.checklist_is_live'))
      invalidate(checklist.id)
    },
    onError: (error) => toast.error(publishError(error, 'Could not publish checklist')),
  })
}

export function usePauseChecklist() {
  const invalidate = useChecklistInvalidate()
  return useMutation({
    mutationFn: (id: string) => checklistsApi.pause(id),
    onSuccess: (checklist) => {
      toast.success(t('checklists.checklist_paused'))
      invalidate(checklist.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not pause checklist')),
  })
}
