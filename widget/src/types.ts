/**
 * Wire types mirrored from the backend widget API
 * (backend/app/api/widget/*.py + app/schemas/tours.py). Kept hand-written and
 * minimal — only the fields the widget actually reads.
 */

import type { Target as DomCaptureTarget } from '@stept/dom-capture'

export type { DomCaptureTarget }

/** Identity handshake the host page may inject (Intercom-style HMAC). */
export interface Identity {
  external_id: string
  email?: string
  name?: string
  hash: string
}

/** `window.SteptSettings` — the host-page configuration object. */
export interface SteptSettings {
  /** The public widget key (`wk_…`); `widgetKey` is accepted as an alias. */
  workspaceKey?: string
  widgetKey?: string
  /** API/asset origin. Defaults to the loader script's own origin. */
  apiBase?: string
  identity?: Identity
  /**
   * Extra origins the AI assistant may navigate to while doing something for the
   * visitor. The host's own origin is always allowed; everything else is refused,
   * so a prompt-injected "go to evil.example" cannot move the session off-site.
   */
  aiAllowedOrigins?: string[]
}

/** Public widget-inbox theming/config (inbox.config passed through boot). */
export interface WidgetConfig {
  accent_color?: string
  greeting?: string
  launcher_position?: 'left' | 'right'
  require_identity?: boolean
  ai_agent_id?: string | null
  [key: string]: unknown
}

export interface BootContact {
  id: string
  name: string
  email: string | null
}

export interface BootWorkspace {
  name: string
  logo_url: string | null
}

export interface ConversationSummary {
  id: string
  status: string
  last_message_preview: string | null
  last_activity_at: string
  unread: boolean
}

export interface BootResponse {
  token: string
  visitor_id: string
  contact: BootContact
  workspace: BootWorkspace
  config: WidgetConfig
  conversations: ConversationSummary[]
  help_center_enabled: boolean
}

export interface RequireIdentityResponse {
  require_identity: true
}

export type BootResult = BootResponse | RequireIdentityResponse

export function isRequireIdentity(r: BootResult): r is RequireIdentityResponse {
  return (r as RequireIdentityResponse).require_identity === true
}

export interface Citation {
  n: number
  title?: string
  url?: string
  document_id?: string
}

export interface WidgetMessage {
  id: string
  direction: 'in' | 'out'
  author_type: 'contact' | 'user' | 'agent' | 'system'
  author_name: string
  content: string
  attachments: Array<Record<string, unknown>>
  created_at: string
  meta: { citations?: Citation[] }
}

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

export interface ArticleRef {
  title: string
  slug: string
}

export interface ArticleCollection {
  name: string
  slug: string
  icon: string | null
  description: string | null
  articles: ArticleRef[]
}

export interface ArticleSearchResult {
  title: string
  slug: string
  snippet: string
}

export interface WidgetArticlesResponse {
  collections: ArticleCollection[]
  results: ArticleSearchResult[]
}

export interface ArticleDetail {
  title: string
  body: string
  collection: { name: string; slug: string } | null
}

export interface CsatOut {
  conversation_id: string
  rating: number
  feedback: string | null
}

/** Tour step + tour, mirrored from schemas/tours.py (WidgetTourOut).
 *
 * v2 fields are optional on the wire type so a pre-v2 payload (and the tests
 * that build minimal fixtures) still typechecks; the player fills defaults. */
export type TourStepType = 'tooltip' | 'modal' | 'banner' | 'hotspot' | 'action' | 'wait'
export type StepPlacement = 'auto' | 'top' | 'bottom' | 'left' | 'right' | 'center'

export interface StepMedia {
  type: 'image' | 'video'
  url: string
}

export interface StepAdvance {
  on: 'button' | 'element_click' | 'input' | 'delay'
  delay_ms?: number | null
}

export interface StepAction {
  kind: 'click' | 'fill' | 'navigate'
  value?: string | null
  url?: string | null
}

export interface StepWait {
  /** The persisted key is `for` (a Python keyword server-side, aliased there). */
  for: 'element' | 'url'
  selector?: string | null
  url_pattern?: string | null
  timeout_ms: number
}

export interface TourStep {
  id: string
  type?: TourStepType
  selector: string
  fallback_selectors?: string[]
  /** Normalized visible text captured at record time (≤80 chars). */
  text_hint?: string
  /** Full @stept/dom-capture Target descriptor (opaque to the backend). */
  target?: DomCaptureTarget | null
  title: string
  body: string
  media?: StepMedia | null
  screenshot_key?: string | null
  /** Public key of the DOM replica used for sandbox playback (not used here). */
  sandbox_key?: string | null
  placement: StepPlacement
  advance?: StepAdvance
  /** Author-supplied button copy. An empty label falls back to Next / Got it. */
  cta?: StepCta | null
  secondary_cta?: StepCta | null
  action?: StepAction | null
  wait?: StepWait | null
}

export interface StepCta {
  label: string
  /** When set, the button opens this instead of advancing. */
  url?: string | null
}

export interface TourSettings {
  mode: 'guided' | 'driven'
  backdrop: boolean
  show_progress: boolean
  dismissable: boolean
}

export type TourKind = 'flow' | 'banner' | 'announcement'

/** Presentation of `banner` steps. Every field is optional: a tour saved before
 * these existed must render as the original full-width accent bar. */
export interface BannerTheme {
  layout?: 'overlay' | 'inline' | null
  full_width?: boolean | null
  max_width?: number | null
  align?: 'start' | 'center' | null
  background?: string | null
  text_color?: string | null
  icon?: string | null
  dismiss?: 'dismiss' | 'never_again' | null
  rounded?: boolean | null
}

export interface Tour {
  id: string
  name: string
  kind?: TourKind
  steps: TourStep[]
  theme: { accent: string; position?: 'top' | 'bottom' | null; banner?: BannerTheme | null }
  version: number
  settings?: TourSettings
  /** `every_time` bypasses the widget's local seen-set. */
  frequency_type?: string
}

export type TourEventName =
  | 'started'
  | 'step_viewed'
  | 'completed'
  | 'dismissed'
  | 'step_error'

/** Telemetry context sent with every tour event (`WidgetTourEventIn.meta`). */
export interface TourEventMeta {
  url?: string
  viewport_w?: number
  /** true when the step resolved via anything but its primary selector. */
  healed?: boolean
  /** step_error only: `not_found` | `in_iframe` | `timeout`. */
  reason?: string
  [key: string]: unknown
}

// --- checklists (schemas/checklists.py → WidgetChecklistOut) -----------------

export interface ChecklistItemAction {
  type: 'start_tour' | 'open_url' | 'open_messenger' | 'none'
  tour_id?: string | null
  url?: string | null
}

export interface ChecklistItemCompletion {
  type: 'manual' | 'tour_completed' | 'url_visited'
  tour_id?: string | null
  url_pattern?: string | null
}

export interface ChecklistItem {
  id: string
  title: string
  body: string
  action: ChecklistItemAction
  completion: ChecklistItemCompletion
}

/** `{item_id: iso_completed_at}` plus the two lifecycle flags. */
export interface ChecklistProgress {
  item_state: Record<string, string>
  dismissed: boolean
  completed: boolean
}

export interface Checklist {
  id: string
  name: string
  description: string
  items: ChecklistItem[]
  theme: { accent: string; position: 'bottom-right' | 'bottom-left' }
  launcher: { label: string; auto_open_once: boolean }
  version: number
  /** Server-side progress; empty for anonymous visitors (kept locally instead). */
  progress?: ChecklistProgress
}

/** POST /checklists/{id}/progress — `stored:false` ⇒ keep local state. */
export interface ChecklistProgressAck {
  stored: boolean
  item_state?: Record<string, string>
  dismissed?: boolean
  completed?: boolean
}

// --- surveys (schemas/surveys.py → WidgetSurveyOut) -------------------------

export type SurveyQuestionType = 'nps' | 'rating' | 'text' | 'select'

export interface SurveyQuestion {
  id: string
  type: SurveyQuestionType
  question: string
  required: boolean
  options?: string[] | null
}

export interface Survey {
  id: string
  name: string
  questions: SurveyQuestion[]
  presentation: 'modal' | 'slideout'
  theme: { accent: string }
  thanks_message: string
  version: number
  frequency_type?: string
}

export interface SurveyAnswer {
  question_id: string
  value: number | string
}

export interface SurveyAck {
  ok: boolean
  thanks_message: string
}

/** GET /api/widget/experiences — the one-call DAP bootstrap. */
export interface ExperiencesResponse {
  tours: Tour[]
  checklists: Checklist[]
  surveys: Survey[]
}

/**
 * Ongoing proactive campaign, mirrored from schemas/campaigns.py
 * (WidgetCampaignOut). Trigger rules are evaluated client-side by the loader.
 */
export interface CampaignTriggerRules {
  /** fnmatch-style glob (`*` wildcards) matched against `location.href`. */
  url_pattern?: string
  /** Seconds on the page before the campaign fires. Absent/0 → immediately. */
  time_on_page_seconds?: number
  [key: string]: unknown
}

export interface Campaign {
  id: string
  message: string
  trigger_rules: CampaignTriggerRules
  sender_name: string
}

/** POST /campaigns/{id}/trigger result (WidgetCampaignTriggerOut). */
export interface CampaignTriggerResult {
  skipped: boolean
  conversation_id: string | null
}

/** Visitor's thumbs rating on an agent/AI answer. */
export type FeedbackRating = 'up' | 'down'

export interface MessageFeedbackAck {
  ok: boolean
  rating: string
}

/** Realtime envelope pushed by /ws/widget. */
export interface RealtimeMessage {
  type: string
  data: Record<string, unknown>
}

// --- in-app assistant (schemas/copilot.py) ----------------------------------

export interface PageContextAck {
  ok: boolean
  /** Is the assistant allowed to look at the page at all? */
  page_control: boolean
  /** May it click and type, or only point? */
  allow_actions: boolean
}

/**
 * A page op the agent is waiting on. Arrives over the realtime socket, and is
 * re-fetchable after a reload (which loses the socket frame).
 */
export interface PendingPageOp {
  run_id: string
  op_id: string
  /** The agent tool that produced it (`page_act`, `show_guide`, …). */
  tool: string
  /** The wire op the host page executes (`snapshot`, `act`, `guide`, `steps`). */
  op: string
  args: Record<string, unknown>
}

export interface CopilotOpAck {
  ok: boolean
  /** `resumed` when the run continued, `ignored` for a stale/duplicate op. */
  status: string
}
