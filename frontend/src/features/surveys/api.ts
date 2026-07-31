/**
 * Surveys API — NPS / rating / text / select micro-surveys shown by the widget.
 *
 * The types below are hand-written mirrors of `app/schemas/surveys.py`
 * (wave 7 lands before `make types` regenerates `@/api/schema`). Field names,
 * optionality and literal unions match the Pydantic models exactly — when the
 * generated schema arrives these can be swapped for
 * `components['schemas']['SurveyOut']` & friends without touching callers.
 */

import { api, ws } from '@/api/client'

export type SurveyStatus = 'draft' | 'live' | 'paused'
export type SurveyPresentation = 'modal' | 'slideout'
export type QuestionType = 'nps' | 'rating' | 'text' | 'select'
export type FrequencyType = 'once' | 'until_completed' | 'until_dismissed' | 'every_time'

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

export interface SurveyTrigger {
  type: 'manual' | 'url_match'
  url_pattern?: string | null
}

export interface SurveyAudience {
  type: 'all' | 'filters'
  /** Omitted by the API when empty — always read it as `filters ?? []`. */
  filters?: SegmentFilter[]
}

/** UTC window; both ends optional (empty schedule = always on). */
export interface SurveySchedule {
  start_at?: string | null
  end_at?: string | null
}

export interface SurveyFrequency {
  type: FrequencyType
  cooldown_hours?: number | null
}

export interface SurveyTheme {
  accent: string
}

/** `SurveyQuestionOut` — server-normalised question (id always present). */
export interface SurveyQuestion {
  id: string
  type: QuestionType
  question: string
  required: boolean
  options?: string[] | null
}

/** `SurveyQuestionIn` — id omitted for new questions; options only on select. */
export interface SurveyQuestionIn {
  id?: string
  type: QuestionType
  question: string
  required: boolean
  options?: string[]
}

export interface Survey {
  id: string
  name: string
  status: string
  questions: SurveyQuestion[]
  presentation: string
  trigger: SurveyTrigger
  audience: SurveyAudience
  schedule: SurveySchedule
  frequency: SurveyFrequency
  priority: number
  theme: SurveyTheme
  thanks_message: string
  version: number
  created_by?: string | null
  created_at: string
  updated_at: string
}

export interface SurveyCreate {
  name: string
  questions?: SurveyQuestionIn[]
  presentation?: SurveyPresentation
  trigger?: SurveyTrigger
  audience?: SurveyAudience
  schedule?: SurveySchedule
  frequency?: SurveyFrequency
  priority?: number
  theme?: SurveyTheme
  thanks_message?: string
}

export interface SurveyUpdate {
  name?: string
  questions?: SurveyQuestionIn[]
  presentation?: SurveyPresentation
  trigger?: SurveyTrigger
  audience?: SurveyAudience
  schedule?: SurveySchedule
  frequency?: SurveyFrequency
  priority?: number
  theme?: SurveyTheme
  thanks_message?: string
}

export interface SurveyAnswer {
  question_id: string
  value: number | string
}

export interface SurveyResponse {
  id: string
  survey_id: string
  contact_id?: string | null
  answers: SurveyAnswer[]
  completed: boolean
  /** Omitted by the API when empty. */
  meta?: Record<string, string>
  created_at: string
}

export interface SurveyDayPoint {
  /** YYYY-MM-DD (UTC) */
  date: string
  responses: number
}

export interface SurveyNpsResult {
  /** %promoters − %detractors, rounded. */
  score: number
  promoters: number
  passives: number
  detractors: number
}

export interface SurveyRatingResult {
  avg: number
  /** keys "1".."5" */
  distribution: Record<string, number>
}

export interface SurveySelectResult {
  question_id: string
  question: string
  counts: Record<string, number>
}

export interface SurveyTextAnswer {
  question_id: string
  value: string
  contact_id?: string | null
  created_at: string
}

export interface SurveyResults {
  responses: number
  completed: number
  completion_rate: number
  by_day: SurveyDayPoint[]
  nps?: SurveyNpsResult | null
  ratings?: SurveyRatingResult | null
  /** Both are omitted by the API when empty — read as `?? []`. */
  select?: SurveySelectResult[]
  text_answers?: SurveyTextAnswer[]
}

/** `OffsetPage[SurveyResponseOut]`. */
export interface ResponsePage {
  items: SurveyResponse[]
  total: number
  limit: number
  offset: number
}

export const surveysApi = {
  list: () => api.get<Survey[]>(ws('/surveys')),
  get: (id: string) => api.get<Survey>(ws(`/surveys/${id}`)),
  create: (body: SurveyCreate) => api.post<Survey>(ws('/surveys'), body),
  update: (id: string, body: SurveyUpdate) => api.patch<Survey>(ws(`/surveys/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/surveys/${id}`)),
  publish: (id: string) => api.post<Survey>(ws(`/surveys/${id}/publish`)),
  pause: (id: string) => api.post<Survey>(ws(`/surveys/${id}/pause`)),
  results: (id: string) => api.get<SurveyResults>(ws(`/surveys/${id}/results`)),
  responses: (id: string, limit: number, offset: number) =>
    api.get<ResponsePage>(ws(`/surveys/${id}/responses`), { query: { limit, offset } }),
}
