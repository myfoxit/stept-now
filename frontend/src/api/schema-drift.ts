/**
 * Drift guard for hand-written API mirrors.
 *
 * A few feature modules declare their own interfaces instead of aliasing
 * `components['schemas'][…]` — they were written while the backend was still in
 * flight and `schema.d.ts` had not been regenerated. Hand-written mirrors are
 * allowed (CLAUDE.md), but a silent divergence from the backend is not: nothing
 * would fail until a field came back `undefined` at runtime.
 *
 * These are type-only assertions with no runtime cost. If the backend changes
 * shape and a mirror is not updated, `tsc` fails here instead of the UI quietly
 * rendering nothing.
 */

import type { components } from '@/api/schema'
import type { Checklist, ChecklistItem, ChecklistStats } from '@/features/checklists/api'
import type {
  Survey,
  SurveyQuestion,
  SurveyResponse,
  SurveyResults,
} from '@/features/surveys/api'

/**
 * What the backend actually returns must satisfy what the UI expects. This is
 * the direction that catches real breakage: a mirror declaring a field the
 * backend dropped, or typing one more narrowly than the API can return.
 *
 * The reverse direction is deliberately NOT asserted — the mirrors mark
 * server-defaulted fields optional (`url_pattern?`, `accent?`) and the pages
 * read them defensively, so being more permissive than the response is fine.
 */
type Delivers<Generated, Mirror> = [Generated] extends [Mirror] ? true : never

type _ChecklistMatches = Delivers<components['schemas']['ChecklistOut'], Checklist>
type _ChecklistItemMatches = Delivers<components['schemas']['ChecklistItemOut'], ChecklistItem>
type _ChecklistStatsMatches = Delivers<components['schemas']['ChecklistStats'], ChecklistStats>
type _SurveyMatches = Delivers<components['schemas']['SurveyOut'], Survey>
type _SurveyQuestionMatches = Delivers<components['schemas']['SurveyQuestionOut'], SurveyQuestion>
type _SurveyResponseMatches = Delivers<components['schemas']['SurveyResponseOut'], SurveyResponse>
type _SurveyResultsMatches = Delivers<components['schemas']['SurveyResults'], SurveyResults>

// `true` for each mirror that still matches; a mismatch resolves to `never` and
// fails to satisfy this tuple.
export const SCHEMA_MIRRORS_MATCH: [
  _ChecklistMatches,
  _ChecklistItemMatches,
  _ChecklistStatsMatches,
  _SurveyMatches,
  _SurveyQuestionMatches,
  _SurveyResponseMatches,
  _SurveyResultsMatches,
] = [true, true, true, true, true, true, true]
