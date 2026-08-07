/**
 * The messenger controller: owns all app state and side effects (API, realtime,
 * the postMessage bridge to the loader). Components stay presentational and call
 * these actions; `subscribe`/`getState` drive re-renders via the `useController`
 * hook.
 */

import {
  ApiError,
  errorCode,
  sendMessageFeedback,
  triggerCampaign,
  WidgetApi,
  widgetWsUrl,
} from '../api'
import { MSG } from '../protocol'
import type {
  ArticleDetail,
  BootContact,
  BootResponse,
  BootWorkspace,
  ConversationSummary,
  FeedbackRating,
  Identity,
  WidgetArticlesResponse,
  WidgetConfig,
  WidgetMessage,
  RealtimeMessage,
} from '../types'
import { isRequireIdentity } from '../types'
import { bridge } from './bridge'
import { WidgetSocket } from './ws'

export interface BootParams {
  workspaceKey: string
  apiBase: string
  identity?: Identity
}

export type Screen =
  | { name: 'loading' }
  | { name: 'identity' }
  | { name: 'error'; message: string }
  | { name: 'home' }
  | { name: 'thread'; conversationId: string | null }
  | { name: 'help' }
  | { name: 'article'; slug: string }

/** A message plus transient UI flags for optimistic sends. */
export type UiMessage = WidgetMessage & { pending?: boolean; failed?: boolean }

/** Where the visitor is in the host app, as reported by the loader. */
export interface PageContext {
  url: string
  path: string
  title: string
}

export interface AppState {
  screen: Screen
  config: WidgetConfig
  workspace: BootWorkspace | null
  contact: BootContact | null
  helpCenter: boolean
  conversations: ConversationSummary[]
  messages: UiMessage[]
  nextCursor: string | null
  loadingMessages: boolean
  agentTyping: boolean
  articles: WidgetArticlesResponse | null
  articleQuery: string
  article: ArticleDetail | null
  loadingArticle: boolean
  csatDone: Record<string, boolean>
  /** Current host page, from the loader's PAGE_CONTEXT messages. */
  page: PageContext | null
  /** True once the backend confirms this agent can see the page. */
  pageControl: boolean
  /** True once the visitor has allowed the assistant to act on the page. */
  actionsAllowed: boolean
  /** Set while a page op is being executed, so the thread can say so. */
  workingOnPage: string | null
}

const INITIAL: AppState = {
  screen: { name: 'loading' },
  config: {},
  workspace: null,
  contact: null,
  helpCenter: false,
  conversations: [],
  messages: [],
  nextCursor: null,
  loadingMessages: false,
  agentTyping: false,
  articles: null,
  articleQuery: '',
  article: null,
  loadingArticle: false,
  csatDone: {},
  page: null,
  pageControl: false,
  actionsAllowed: false,
  workingOnPage: null,
}

export class Controller {
  private state: AppState = INITIAL
  private listeners = new Set<() => void>()
  private params: BootParams
  private api: WidgetApi
  private socket: WidgetSocket | null = null
  private token: string | null = null
  private unbindBridge: (() => void) | null = null
  private seenIds = new Set<string>()
  private typingTimer: ReturnType<typeof setTimeout> | null = null
  private agentTypingTimer: ReturnType<typeof setTimeout> | null = null
  /** opId → the run waiting on it, while the loader executes the op. */
  private pendingOps = new Map<string, { runId: string; op: string }>()

  constructor(params: BootParams) {
    this.params = params
    this.api = new WidgetApi(params.apiBase)
  }

  /** Public widget key — namespaces per-widget localStorage (e.g. feedback). */
  get widgetKey(): string {
    return this.params.workspaceKey
  }

  /** Detach global listeners (bridge + socket). Teardown/test hook. */
  dispose(): void {
    this.unbindBridge?.()
    this.unbindBridge = null
    this.socket?.close()
    this.socket = null
  }

  // --- store plumbing ------------------------------------------------------

  getState(): AppState {
    return this.state
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<AppState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }

  // --- boot ----------------------------------------------------------------

  async boot(): Promise<void> {
    if (!this.params.workspaceKey) {
      this.set({ screen: { name: 'error', message: 'Missing widget key.' } })
      return
    }
    try {
      const result = await this.api.boot({
        widget_key: this.params.workspaceKey,
        visitor_id: this.storedVisitorId(),
        identity: this.params.identity,
      })
      if (isRequireIdentity(result)) {
        this.set({ screen: { name: 'identity' } })
        return
      }
      this.applyBoot(result)
    } catch (err) {
      this.set({ screen: { name: 'error', message: this.describe(err) } })
    }
  }

  private applyBoot(boot: BootResponse): void {
    this.token = boot.token
    this.api.token = boot.token
    this.storeVisitorId(boot.visitor_id)
    this.set({
      screen: { name: 'home' },
      config: boot.config || {},
      workspace: boot.workspace,
      contact: boot.contact,
      helpCenter: boot.help_center_enabled,
      conversations: boot.conversations,
    })
    this.connectRealtime()
    this.bindBridge()
    this.emitReady()
  }

  /** React to loader → app messages (refresh on open; trigger due campaigns). */
  private bindBridge(): void {
    this.unbindBridge?.()
    this.unbindBridge = bridge.on((env) => {
      if (env.type === MSG.OPEN) {
        void this.refreshConversations()
      } else if (env.type === MSG.CAMPAIGN_DUE) {
        const payload = (env.payload || {}) as { campaignId?: unknown }
        if (typeof payload.campaignId === 'string' && payload.campaignId) {
          void this.onCampaignDue(payload.campaignId)
        }
      } else if (env.type === MSG.PAGE_CONTEXT) {
        this.onPageContext((env.payload || {}) as Record<string, unknown>)
      } else if (env.type === MSG.COPILOT_RESULT) {
        this.onCopilotResult((env.payload || {}) as Record<string, unknown>)
      }
    })
  }

  // --- in-app assistant ----------------------------------------------------

  /**
   * The loader reported the visitor's page (on boot and every SPA navigation).
   *
   * Kept in state for the UI and forwarded to the backend, which stores it on the
   * conversation so the agent's next turn knows which screen "here" means.
   */
  private onPageContext(payload: Record<string, unknown>): void {
    const url = typeof payload.url === 'string' ? payload.url : ''
    if (!url) return
    const page: PageContext = {
      url,
      path: typeof payload.path === 'string' ? payload.path : '',
      title: typeof payload.title === 'string' ? payload.title : '',
    }
    this.set({ page })
    void this.pushPageContext()
  }

  /** Send the current page (and optionally a consent decision) to the backend. */
  private async pushPageContext(allowActions?: boolean): Promise<void> {
    const { page, screen } = this.state
    const conversationId = screen.name === 'thread' ? screen.conversationId : null
    if (!page || !conversationId) return
    try {
      const ack = await this.api.setPageContext(conversationId, {
        url: page.url,
        title: page.title,
        path: page.path,
        allowActions,
      })
      this.set({ pageControl: ack.page_control, actionsAllowed: ack.allow_actions })
    } catch {
      /* page context is an enhancement — a failure just means less context */
    }
  }

  /**
   * Let the assistant act on the page (or take that permission back).
   *
   * Consent lives on the conversation server-side, so it survives a reload and
   * is re-read by whichever worker runs the next turn.
   */
  async setActionsAllowed(allowed: boolean): Promise<void> {
    this.set({ actionsAllowed: allowed })
    await this.pushPageContext(allowed)
  }

  /**
   * The agent asked the host page to do something.
   *
   * The op is forwarded over the bridge to the loader (only it can touch the host
   * DOM) and the result is POSTed back to resume the parked run. A `guide` op is
   * special-cased into the existing tour-start path so an AI-recommended tour
   * plays with real telemetry against its tour row.
   */
  private onCopilotOp(data: Record<string, unknown>): void {
    const runId = typeof data.run_id === 'string' ? data.run_id : ''
    const opId = typeof data.op_id === 'string' ? data.op_id : ''
    const op = typeof data.op === 'string' ? data.op : ''
    if (!runId || !opId || !op) return
    const args = (data.args || {}) as Record<string, unknown>
    this.set({ workingOnPage: op })

    if (op === 'guide') {
      const tourId = typeof args.tour_id === 'string' ? args.tour_id : ''
      bridge.post(MSG.TOUR_START, { tourId })
      void this.finishOp(runId, opId, {
        ok: Boolean(tourId),
        ...(tourId ? { note: 'the guide is now playing on the page' } : { error: 'missing tour_id' }),
      })
      return
    }
    if (op === 'steps') {
      bridge.post(MSG.GUIDE_START, { name: args.title, steps: args.steps })
      void this.finishOp(runId, opId, {
        ok: true,
        note: 'the walkthrough is now showing on the page',
      })
      return
    }

    this.pendingOps.set(opId, { runId, op })
    bridge.post(MSG.COPILOT_OP, { opId, op, args })
  }

  /** The loader answered a page op — pass the result back to the agent run. */
  private onCopilotResult(payload: Record<string, unknown>): void {
    const opId = typeof payload.opId === 'string' ? payload.opId : ''
    const pending = opId ? this.pendingOps.get(opId) : undefined
    if (!pending) return
    this.pendingOps.delete(opId)
    void this.finishOp(pending.runId, opId, (payload.result ?? {}) as Record<string, unknown>)
  }

  private async finishOp(runId: string, opId: string, result: unknown): Promise<void> {
    const { screen } = this.state
    const conversationId = screen.name === 'thread' ? screen.conversationId : null
    this.set({ workingOnPage: null })
    if (!conversationId) return
    try {
      await this.api.submitOpResult(conversationId, { run_id: runId, op_id: opId, result })
    } catch {
      // The run is left to the server-side sweep, which resumes it with a
      // timeout error rather than stranding the conversation.
    }
  }

  /**
   * After (re)opening a thread, ask whether a page op is outstanding.
   *
   * A reload drops the websocket frame that carried it, so without this the run
   * would sit parked until the sweep timed it out — the visitor would watch the
   * assistant stall mid-walkthrough for no visible reason.
   */
  private async resumePendingOp(conversationId: string): Promise<void> {
    try {
      const pending = await this.api.getPendingOp(conversationId)
      if (pending) {
        this.onCopilotOp({
          run_id: pending.run_id,
          op_id: pending.op_id,
          op: pending.op,
          args: pending.args,
        })
      }
    } catch {
      /* nothing outstanding, or offline */
    }
  }

  /**
   * The loader says an ongoing campaign is due: POST the trigger with our
   * visitor token. When the backend created the proactive conversation, refresh
   * the list (home screen updates if visible) and push the unread count so the
   * launcher badge bumps. Skipped/error outcomes are silently ignored — the
   * loader already marked the campaign seen. Never opens the panel.
   */
  private async onCampaignDue(campaignId: string): Promise<void> {
    if (!this.token) return
    try {
      const result = await triggerCampaign(this.params.apiBase, this.token, campaignId)
      if (result.conversation_id) await this.refreshConversations()
    } catch {
      /* skipped / disabled / race — non-intrusive by design */
    }
  }

  private emitReady(): void {
    bridge.post(MSG.READY, {
      token: this.token,
      accent: this.state.config.accent_color,
      position: this.state.config.launcher_position,
      unread: this.unreadCount(),
    })
  }

  private connectRealtime(): void {
    if (!this.token) return
    this.socket = new WidgetSocket(widgetWsUrl(this.params.apiBase, this.token), (msg) =>
      this.onRealtime(msg),
    )
    this.socket.connect()
  }

  // --- navigation ----------------------------------------------------------

  goHome(): void {
    this.set({ screen: { name: 'home' } })
    this.refreshConversations()
  }

  async refreshConversations(): Promise<void> {
    try {
      const conversations = await this.api.listConversations()
      this.set({ conversations })
      this.pushUnread()
    } catch {
      /* keep the cached list */
    }
  }

  startNewConversation(): void {
    this.seenIds.clear()
    this.set({
      screen: { name: 'thread', conversationId: null },
      messages: [],
      nextCursor: null,
      agentTyping: false,
    })
  }

  async openConversation(id: string): Promise<void> {
    this.seenIds.clear()
    this.set({
      screen: { name: 'thread', conversationId: id },
      messages: [],
      nextCursor: null,
      loadingMessages: true,
      agentTyping: false,
    })
    try {
      const page = await this.api.listMessages(id)
      const items = page.items as UiMessage[]
      items.forEach((m) => this.seenIds.add(m.id))
      this.set({ messages: items, nextCursor: page.next_cursor, loadingMessages: false })
      this.socket?.subscribe(id)
      void this.markRead(id)
      void this.pushPageContext()
      void this.resumePendingOp(id)
    } catch (err) {
      this.set({ loadingMessages: false, screen: { name: 'error', message: this.describe(err) } })
    }
  }

  async loadOlderMessages(): Promise<void> {
    const { screen, nextCursor } = this.state
    if (screen.name !== 'thread' || !screen.conversationId || !nextCursor) return
    try {
      const page = await this.api.listMessages(screen.conversationId, nextCursor)
      const older = (page.items as UiMessage[]).filter((m) => !this.seenIds.has(m.id))
      older.forEach((m) => this.seenIds.add(m.id))
      this.set({ messages: [...older, ...this.state.messages], nextCursor: page.next_cursor })
    } catch {
      /* ignore */
    }
  }

  // --- sending -------------------------------------------------------------

  async send(text: string): Promise<void> {
    const content = text.trim()
    if (!content) return
    const { screen } = this.state
    if (screen.name !== 'thread') return

    const temp: UiMessage = {
      id: `tmp-${Date.now()}`,
      direction: 'in',
      author_type: 'contact',
      author_name: this.state.contact?.name || 'You',
      content,
      attachments: [],
      created_at: new Date().toISOString(),
      meta: {},
      pending: true,
    }
    this.set({ messages: [...this.state.messages, temp] })

    try {
      if (screen.conversationId === null) {
        const summary = await this.api.createConversation(content)
        this.set({
          screen: { name: 'thread', conversationId: summary.id },
          conversations: [summary, ...this.state.conversations],
        })
        this.socket?.subscribe(summary.id)
        // The agent's first turn happens now, so it needs to know which screen the
        // visitor is on before it decides whether to guide or explain.
        void this.pushPageContext()
        // Reload authoritative history (drops the temp, includes the real message).
        this.seenIds.clear()
        const page = await this.api.listMessages(summary.id)
        const items = page.items as UiMessage[]
        items.forEach((m) => this.seenIds.add(m.id))
        this.set({ messages: items, nextCursor: page.next_cursor })
      } else {
        const real = (await this.api.sendMessage(screen.conversationId, content)) as UiMessage
        this.replaceTemp(temp.id, real)
      }
    } catch {
      this.markFailed(temp.id)
    }
  }

  emitTyping(isTyping: boolean): void {
    const { screen } = this.state
    if (screen.name !== 'thread' || !screen.conversationId || !this.socket) return
    const id = screen.conversationId
    this.socket.typing(id, isTyping)
    if (isTyping) {
      if (this.typingTimer) clearTimeout(this.typingTimer)
      this.typingTimer = setTimeout(() => this.socket?.typing(id, false), 4000)
    }
  }

  private async markRead(id: string): Promise<void> {
    try {
      await this.api.markRead(id)
      this.set({
        conversations: this.state.conversations.map((c) =>
          c.id === id ? { ...c, unread: false } : c,
        ),
      })
      this.pushUnread()
    } catch {
      /* ignore */
    }
  }

  private replaceTemp(tempId: string, real: UiMessage): void {
    if (this.seenIds.has(real.id)) {
      this.set({ messages: this.state.messages.filter((m) => m.id !== tempId) })
      return
    }
    this.seenIds.add(real.id)
    this.set({ messages: this.state.messages.map((m) => (m.id === tempId ? real : m)) })
  }

  private markFailed(tempId: string): void {
    this.set({
      messages: this.state.messages.map((m) =>
        m.id === tempId ? { ...m, pending: false, failed: true } : m,
      ),
    })
  }

  // --- realtime ------------------------------------------------------------

  private onRealtime(msg: RealtimeMessage): void {
    if (msg.type === 'message.created') this.onMessageCreated(msg.data)
    else if (msg.type === 'typing') this.onTyping(msg.data)
    else if (msg.type === 'conversation.updated') this.onConversationUpdated(msg.data)
    else if (msg.type === 'copilot.op') this.onCopilotOp(msg.data)
  }

  private onMessageCreated(data: Record<string, unknown>): void {
    const raw = data.message as (WidgetMessage & { visibility?: string; conversation_id?: string }) | undefined
    if (!raw || raw.visibility !== 'public') return
    const convId = raw.conversation_id
    const active = this.state.screen.name === 'thread' ? this.state.screen.conversationId : null

    if (convId && convId === active && !this.seenIds.has(raw.id)) {
      this.seenIds.add(raw.id)
      const message: UiMessage = {
        id: raw.id,
        direction: raw.direction,
        author_type: raw.author_type,
        author_name: raw.author_name,
        content: raw.content,
        attachments: raw.attachments || [],
        created_at: raw.created_at,
        meta: { citations: raw.meta?.citations },
      }
      this.set({ messages: [...this.state.messages, message], agentTyping: false })
      if (raw.direction === 'out') void this.markRead(convId)
    } else if (convId && raw.direction === 'out') {
      // A reply landed in a conversation we're not viewing → mark unread + refresh.
      this.set({
        conversations: this.state.conversations.map((c) =>
          c.id === convId ? { ...c, unread: true, last_message_preview: raw.content.slice(0, 140) } : c,
        ),
      })
      this.pushUnread()
    }
  }

  private onTyping(data: Record<string, unknown>): void {
    const active = this.state.screen.name === 'thread' ? this.state.screen.conversationId : null
    if (data.conversation_id !== active || data.source === 'contact') return
    const isTyping = Boolean(data.is_typing)
    this.set({ agentTyping: isTyping })
    if (this.agentTypingTimer) clearTimeout(this.agentTypingTimer)
    if (isTyping) this.agentTypingTimer = setTimeout(() => this.set({ agentTyping: false }), 6000)
  }

  private onConversationUpdated(data: Record<string, unknown>): void {
    const id = data.id as string | undefined
    if (!id) return
    this.set({
      conversations: this.state.conversations.map((c) =>
        c.id === id ? { ...c, status: (data.status as string) ?? c.status } : c,
      ),
    })
  }

  // --- help center ---------------------------------------------------------

  async openHelp(): Promise<void> {
    this.set({ screen: { name: 'help' }, articleQuery: '' })
    if (!this.state.articles) await this.loadArticles('')
  }

  async searchArticles(query: string): Promise<void> {
    this.set({ articleQuery: query })
    await this.loadArticles(query)
  }

  private async loadArticles(query: string): Promise<void> {
    try {
      const articles = await this.api.getArticles(query)
      this.set({ articles })
    } catch {
      this.set({ articles: { collections: [], results: [] } })
    }
  }

  async openArticle(slug: string): Promise<void> {
    this.set({ screen: { name: 'article', slug }, article: null, loadingArticle: true })
    try {
      const article = await this.api.getArticle(slug)
      this.set({ article, loadingArticle: false })
    } catch (err) {
      this.set({ loadingArticle: false, screen: { name: 'error', message: this.describe(err) } })
    }
  }

  backToHelp(): void {
    this.set({ screen: { name: 'help' }, article: null })
  }

  // --- csat ----------------------------------------------------------------

  async submitCsat(conversationId: string, rating: number, feedback?: string): Promise<void> {
    try {
      await this.api.submitCsat(conversationId, rating, feedback)
      this.set({ csatDone: { ...this.state.csatDone, [conversationId]: true } })
    } catch {
      /* surfaced as no state change; UI can retry */
    }
  }

  // --- answer feedback (AI thumbs) ------------------------------------------

  /**
   * POST the visitor's thumbs rating for an agent/AI answer in the open thread.
   * The bubble keeps the chosen state locally (component state + localStorage);
   * the backend upserts, so switching ratings just re-posts.
   */
  async submitMessageFeedback(messageId: string, rating: FeedbackRating): Promise<void> {
    const { screen } = this.state
    if (screen.name !== 'thread' || !screen.conversationId || !this.token) return
    try {
      await sendMessageFeedback(this.params.apiBase, this.token, screen.conversationId, messageId, rating)
    } catch {
      /* non-blocking — the thumb stays selected locally */
    }
  }

  // --- host bridge open/close ---------------------------------------------

  requestClose(): void {
    bridge.post(MSG.CLOSE, {})
  }

  // --- helpers -------------------------------------------------------------

  private unreadCount(): number {
    return this.state.conversations.filter((c) => c.unread).length
  }

  private pushUnread(): void {
    bridge.post(MSG.UNREAD, { count: this.unreadCount() })
  }

  private storedVisitorId(): string | null {
    try {
      return window.localStorage.getItem(this.visitorKey())
    } catch {
      return null
    }
  }

  private storeVisitorId(id: string): void {
    try {
      window.localStorage.setItem(this.visitorKey(), id)
    } catch {
      /* private mode — session-only */
    }
  }

  private visitorKey(): string {
    return `stept:visitor:${this.params.workspaceKey}`
  }

  private describe(err: unknown): string {
    if (err instanceof ApiError) {
      if (err.status === 404) return 'This chat widget is unavailable.'
      // A blocked visitor gets the same neutral wording as an unavailable
      // widget: telling them they're blocked is hostile and confirms the block.
      if (errorCode(err) === 'contact_blocked') return 'Chat is unavailable right now.'
      return err.message || 'Something went wrong.'
    }
    return 'Unable to reach the chat service.'
  }
}
