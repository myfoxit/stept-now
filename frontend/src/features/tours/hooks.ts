import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { useRealtime } from '@/api/ws'
import { currentWorkspaceId } from '@/stores/auth'

import {
  toursApi,
  type Tour,
  type TourCreate,
  type TourEvent,
  type TourEventsPage,
  type TourUpdate,
} from './api'
import { duplicatePayload, prependEvent } from './lib'

const AREA = 'tours'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

// --- queries ----------------------------------------------------------------

export function useTours() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: [AREA, workspaceId], queryFn: toursApi.list })
}

export function useTour(id: string | undefined) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id],
    queryFn: () => toursApi.get(id!),
    enabled: !!id,
  })
}

export function useTourStats(id: string | undefined, enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id, 'stats'],
    queryFn: () => toursApi.stats(id!),
    enabled: !!id && enabled,
  })
}

export function useTourEvents(
  id: string | undefined,
  { limit = 25, offset = 0 }: { limit?: number; offset?: number } = {}
) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: [AREA, workspaceId, id, 'events', limit, offset],
    queryFn: () => toursApi.events(id!, limit, offset),
    enabled: !!id,
    placeholderData: keepPreviousData,
  })
}

/**
 * Live analytics: a `tour.event` broadcast for THIS tour is prepended to the
 * newest-first first page (so the table grows without a refetch) and
 * invalidates the stats query so the KPI tiles and funnel catch up.
 */
export function useLiveTourEvents(id: string | undefined, limit = 25) {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const handler = useCallback(
    (data: Record<string, unknown>) => {
      if (!id || data.tour_id !== id) return
      const event = data as unknown as TourEvent
      queryClient.setQueryData<TourEventsPage>(
        [AREA, workspaceId, id, 'events', limit, 0],
        (page) => (page ? prependEvent(page, event) : page)
      )
      void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, id, 'stats'] })
    },
    [queryClient, workspaceId, id, limit]
  )
  useRealtime('tour.event', handler)
}

// --- mutations --------------------------------------------------------------

/** Invalidate the list plus, when given, exactly one tour's detail + analytics. */
function useToursInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId], exact: true })
    if (id) void queryClient.invalidateQueries({ queryKey: [AREA, workspaceId, id] })
  }
}

export function useCreateTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: (body: TourCreate) => toursApi.create(body),
    onSuccess: () => {
      toast.success('Tour created')
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create tour')),
  })
}

/** Copy an existing tour (steps, targeting, schedule) into a new draft. */
export function useDuplicateTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: (tour: Tour) => toursApi.create(duplicatePayload(tour)),
    onSuccess: (tour) => {
      toast.success(`Duplicated as “${tour.name}”`)
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not duplicate tour')),
  })
}

export function useUpdateTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TourUpdate }) => toursApi.update(id, body),
    onSuccess: (tour) => {
      toast.success('Tour saved')
      invalidate(tour.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save tour')),
  })
}

export function useDeleteTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: (id: string) => toursApi.remove(id),
    onSuccess: () => {
      toast.success('Tour deleted')
      invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete tour')),
  })
}

export function usePublishTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: (id: string) => toursApi.publish(id),
    onSuccess: (tour) => {
      toast.success('Tour published')
      invalidate(tour.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not publish tour')),
  })
}

export function usePauseTour() {
  const invalidate = useToursInvalidate()
  return useMutation({
    mutationFn: (id: string) => toursApi.pause(id),
    onSuccess: (tour) => {
      toast.success('Tour paused')
      invalidate(tour.id)
    },
    onError: (error) => toast.error(errMessage(error, 'Could not pause tour')),
  })
}

/**
 * The recorder build available for download. Workspace-independent, so it sits
 * outside the `[AREA, workspaceId]` key space and is cached for the session.
 */
export function useExtensionRelease() {
  return useQuery({
    queryKey: [AREA, 'extension-release'],
    queryFn: toursApi.extensionRelease,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

export function useRecorderToken() {
  return useMutation({
    mutationFn: () => toursApi.recorderToken(),
    onError: (error) => toast.error(errMessage(error, 'Could not mint recorder token')),
  })
}

export function usePreviewToken() {
  return useMutation({
    mutationFn: (id: string) => toursApi.previewToken(id),
    onError: (error) => toast.error(errMessage(error, 'Could not create a preview link')),
  })
}

export function useUploadStepMedia() {
  return useMutation({
    mutationFn: (file: File) => toursApi.uploadMedia(file),
    onError: (error) => toast.error(errMessage(error, 'Could not upload that file')),
  })
}
