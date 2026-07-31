import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Tour = components['schemas']['TourOut']
export type TourCreate = components['schemas']['TourCreate']
export type TourUpdate = components['schemas']['TourUpdate']
export type TourStats = components['schemas']['TourStats']
export type TourStep = components['schemas']['TourStepOut']
export type RecorderToken = components['schemas']['RecorderTokenOut']

export const toursApi = {
  list: () => api.get<Tour[]>(ws('/tours')),
  get: (id: string) => api.get<Tour>(ws(`/tours/${id}`)),
  create: (body: TourCreate) => api.post<Tour>(ws('/tours'), body),
  update: (id: string, body: TourUpdate) => api.patch<Tour>(ws(`/tours/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/tours/${id}`)),
  publish: (id: string) => api.post<Tour>(ws(`/tours/${id}/publish`)),
  pause: (id: string) => api.post<Tour>(ws(`/tours/${id}/pause`)),
  stats: (id: string) => api.get<TourStats>(ws(`/tours/${id}/stats`)),
  recorderToken: () => api.post<RecorderToken>(ws('/tours/recorder-token')),
}
