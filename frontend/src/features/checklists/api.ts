/**
 * Checklists API — onboarding checklists shown by the widget launcher.
 *
 * The types below are hand-written mirrors of `app/schemas/checklists.py`
 * (wave 7 lands before `make types` regenerates `@/api/schema`). Field names,
 * optionality and literal unions match the Pydantic models exactly — when the
 * generated schema arrives these can be swapped for
 * `components['schemas']['ChecklistOut']` & friends without touching callers.
 */

import { api, ws } from '@/api/client'

export type ChecklistStatus = 'draft' | 'live' | 'paused'
export type ChecklistPosition = 'bottom-right' | 'bottom-left'
export type ChecklistActionType = 'start_tour' | 'open_url' | 'open_messenger' | 'none'
export type ChecklistCompletionType = 'manual' | 'tour_completed' | 'url_visited'

/** Mirrors `SegmentFilter` — the shared audience filter DSL. */
export type FilterOp =
  | 'eq'
  | 'neq'
  | 'contains'
  | 'starts_with'
  | 'exists'
  | 'not_exists'
  | 'gt'
  | 'lt'

export interface SegmentFilter {
  field: string
  op: FilterOp
  value?: unknown
}

export interface ChecklistTrigger {
  type: 'manual' | 'url_match'
  url_pattern?: string | null
}

export interface ChecklistAudience {
  type: 'all' | 'filters'
  /** Omitted by the API when empty — always read it as `filters ?? []`. */
  filters?: SegmentFilter[]
}

export interface ChecklistTheme {
  accent: string
  position: ChecklistPosition
}

export interface ChecklistLauncher {
  label: string
  auto_open_once: boolean
}

export interface ChecklistItemAction {
  type: ChecklistActionType
  tour_id?: string | null
  url?: string | null
}

export interface ChecklistItemCompletion {
  type: ChecklistCompletionType
  tour_id?: string | null
  url_pattern?: string | null
}

/** `ChecklistItemOut` — server-normalised item (id always present). */
export interface ChecklistItem {
  id: string
  title: string
  body: string
  action: ChecklistItemAction
  completion: ChecklistItemCompletion
}

/** `ChecklistItemIn` — id omitted for new items. */
export interface ChecklistItemIn {
  id?: string
  title: string
  body: string
  action: ChecklistItemAction
  completion: ChecklistItemCompletion
}

export interface Checklist {
  id: string
  name: string
  description: string
  status: string
  items: ChecklistItem[]
  trigger: ChecklistTrigger
  audience: ChecklistAudience
  theme: ChecklistTheme
  launcher: ChecklistLauncher
  priority: number
  version: number
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface ChecklistCreate {
  name: string
  description?: string
  items?: ChecklistItemIn[]
  trigger?: ChecklistTrigger
  audience?: ChecklistAudience
  theme?: ChecklistTheme
  launcher?: ChecklistLauncher
  priority?: number
}

export interface ChecklistUpdate {
  name?: string
  description?: string
  items?: ChecklistItemIn[]
  trigger?: ChecklistTrigger
  audience?: ChecklistAudience
  theme?: ChecklistTheme
  launcher?: ChecklistLauncher
  priority?: number
}

export interface ChecklistItemStat {
  id: string
  title: string
  completed_count: number
}

export interface ChecklistStats {
  /** Reserved server-side — the widget does not report impressions yet. */
  views?: number | null
  starts: number
  completions: number
  completion_rate: number
  items: ChecklistItemStat[]
}

/**
 * Minimal projection of `TourOut` for the item action/completion tour pickers.
 * Deliberately local: `features/tours` is owned by another agent, so this
 * feature never imports from it.
 */
export interface TourOption {
  id: string
  name: string
  status: string
}

export const checklistsApi = {
  list: () => api.get<Checklist[]>(ws('/checklists')),
  get: (id: string) => api.get<Checklist>(ws(`/checklists/${id}`)),
  create: (body: ChecklistCreate) => api.post<Checklist>(ws('/checklists'), body),
  update: (id: string, body: ChecklistUpdate) =>
    api.patch<Checklist>(ws(`/checklists/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/checklists/${id}`)),
  publish: (id: string) => api.post<Checklist>(ws(`/checklists/${id}/publish`)),
  pause: (id: string) => api.post<Checklist>(ws(`/checklists/${id}/pause`)),
  stats: (id: string) => api.get<ChecklistStats>(ws(`/checklists/${id}/stats`)),
}

/** Tour options for the item pickers (only id/name/status are read). */
export const listTourOptions = () => api.get<TourOption[]>(ws('/tours'))
