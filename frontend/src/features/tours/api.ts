import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Tour = components['schemas']['TourOut']
export type TourCreate = components['schemas']['TourCreate']
export type TourUpdate = components['schemas']['TourUpdate']
export type TourStats = components['schemas']['TourStats']
export type TourStepStat = components['schemas']['TourStepStat']
export type TourDayStat = components['schemas']['TourDayStat']
export type TourStep = components['schemas']['TourStepOut']
export type TourStepIn = components['schemas']['TourStepIn']
export type TourEvent = components['schemas']['TourEventOut']
export type TourEventsPage = components['schemas']['OffsetPage_TourEventOut_']
export type RecorderToken = components['schemas']['RecorderTokenOut']
export type PreviewToken = components['schemas']['PreviewTokenOut']
export type UploadedFile = components['schemas']['FileOut']
export type SegmentFilter = components['schemas']['SegmentFilter']
export type FilterOp = SegmentFilter['op']
export type TourKind = NonNullable<TourCreate['kind']>
export type StepType = NonNullable<TourStepIn['type']>
export type StepPlacement = NonNullable<TourStepIn['placement']>

export const toursApi = {
  list: () => api.get<Tour[]>(ws('/tours')),
  get: (id: string) => api.get<Tour>(ws(`/tours/${id}`)),
  create: (body: TourCreate) => api.post<Tour>(ws('/tours'), body),
  update: (id: string, body: TourUpdate) => api.patch<Tour>(ws(`/tours/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/tours/${id}`)),
  publish: (id: string) => api.post<Tour>(ws(`/tours/${id}/publish`)),
  pause: (id: string) => api.post<Tour>(ws(`/tours/${id}/pause`)),
  stats: (id: string) => api.get<TourStats>(ws(`/tours/${id}/stats`)),
  events: (id: string, limit: number, offset: number) =>
    api.get<TourEventsPage>(ws(`/tours/${id}/events`), { query: { limit, offset } }),
  recorderToken: () => api.post<RecorderToken>(ws('/tours/recorder-token')),
  previewToken: (id: string) => api.post<PreviewToken>(ws(`/tours/${id}/preview-token`)),
  /**
   * Step media must render on the customer's site, where no Stept session
   * exists — `?public=true` stores it in the unauthenticated media namespace.
   */
  uploadMedia: (file: File) => api.upload<UploadedFile>(ws('/files?public=true'), file),
}
