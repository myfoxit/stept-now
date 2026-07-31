import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { useRealtime } from '@/api/ws'
import { currentWorkspaceId } from '@/stores/auth'

import { surveysApi, type SurveyCreate, type SurveyUpdate } from './api'

const AREA = 'surveys'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** Publishing a question-less survey is a 409 — say what to do about it. */
function publishError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 409) {
    return 'Add at least one question before publishing'
  }
  return errMessage(error, fallback)
}

// --- queries ----------------------------------------------------------------

export function useSurveys(enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId], queryFn: surveysApi.list, enabled })
}

export function useSurvey(id: string | undefined) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id],
    queryFn: () => surveysApi.get(id!),
    enabled: Boolean(id),
  })
}

export function useSurveyResults(id: string | undefined, enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id, 'results'],
    queryFn: () => surveysApi.results(id!),
    enabled: Boolean(id) && enabled,
  })
}

export function useSurveyResponses(
  id: string | undefined,
  { limit = 10, offset = 0, enabled = true }: { limit?: number; offset?: number; enabled?: boolean }
) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id, 'responses', limit, offset],
    queryFn: () => surveysApi.responses(id!, limit, offset),
    enabled: Boolean(id) && enabled,
    placeholderData: keepPreviousData,
  })
}

/**
 * Live results: a `survey.submitted` broadcast for THIS survey refetches the
 * results + responses queries so the dashboard appends without a reload.
 */
export function useLiveSurveyResults(id: string | undefined) {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const handler = useCallback(
    (data: Record<string, unknown>) => {
      if (!id) return
      if (data.survey_id && data.survey_id !== id) return
      void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, id] })
    },
    [queryClient, workspaceId, id]
  )
  useRealtime('survey.submitted', handler)
}

// --- mutations --------------------------------------------------------------

/** Invalidate the list plus, when given, exactly one survey's detail + results. */
function useSurveyInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId], exact: true })
    if (id) void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, id] })
  }
}

export function useCreateSurvey() {
  const invalidate = useSurveyInvalidate()
  return useMutation({
    mutationFn: (body: SurveyCreate) => surveysApi.create(body),
    onSuccess: () => {
      toast.success('Survey created')
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create survey')),
  })
}

export function useUpdateSurvey() {
  const invalidate = useSurveyInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SurveyUpdate }) => surveysApi.update(id, body),
    onSuccess: (survey) => {
      toast.success('Survey saved')
      invalidate(survey.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save survey')),
  })
}

export function useDeleteSurvey() {
  const invalidate = useSurveyInvalidate()
  return useMutation({
    mutationFn: (id: string) => surveysApi.remove(id),
    onSuccess: () => {
      toast.success('Survey deleted')
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete survey')),
  })
}

export function usePublishSurvey() {
  const invalidate = useSurveyInvalidate()
  return useMutation({
    mutationFn: (id: string) => surveysApi.publish(id),
    onSuccess: (survey) => {
      toast.success('Survey is live')
      invalidate(survey.id)
    },
    onError: (error) => toast.error(publishError(error, 'Could not publish survey')),
  })
}

export function usePauseSurvey() {
  const invalidate = useSurveyInvalidate()
  return useMutation({
    mutationFn: (id: string) => surveysApi.pause(id),
    onSuccess: (survey) => {
      toast.success('Survey paused')
      invalidate(survey.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not pause survey')),
  })
}
