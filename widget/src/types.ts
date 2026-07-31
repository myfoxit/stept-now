/**
 * Wire types mirrored from the backend widget API
 * (backend/app/api/widget/*.py + app/schemas/tours.py). Kept hand-written and
 * minimal — only the fields the widget actually reads.
 */

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
}

/** Public widget-inbox theming/config (inbox.config passed through boot). */
export interface WidgetConfig {
  accent_color?: string
  greeting?: string
  launcher_position?: 'left' | 'right'
  require_identity?: boolean
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

/** Tour step + tour, mirrored from schemas/tours.py (WidgetTourOut). */
export interface TourStep {
  id: string
  selector: string
  title: string
  body: string
  placement: 'auto' | 'top' | 'bottom' | 'left' | 'right'
}

export interface Tour {
  id: string
  name: string
  steps: TourStep[]
  theme: { accent: string }
  version: number
}

export type TourEventName = 'started' | 'step_viewed' | 'completed' | 'dismissed'

/** Realtime envelope pushed by /ws/widget. */
export interface RealtimeMessage {
  type: string
  data: Record<string, unknown>
}
