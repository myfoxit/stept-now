import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import { toursApi, type TourCreate, type TourUpdate } from './api'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function useTours() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: ['tours', workspaceId], queryFn: toursApi.list })
}

export function useTour(id: string | undefined) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['tours', workspaceId, id],
    queryFn: () => toursApi.get(id!),
    enabled: !!id,
  })
}

export function useTourStats(id: string, enabled = true) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['tours', workspaceId, id, 'stats'],
    queryFn: () => toursApi.stats(id),
    enabled,
  })
}

function useToursInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return (id?: string) => {
    void queryClient.invalidateQueries({ queryKey: ['tours', workspaceId] })
    if (id) void queryClient.invalidateQueries({ queryKey: ['tours', workspaceId, id] })
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

export function useRecorderToken() {
  return useMutation({
    mutationFn: () => toursApi.recorderToken(),
    onError: (error) => toast.error(errMessage(error, 'Could not mint recorder token')),
  })
}
