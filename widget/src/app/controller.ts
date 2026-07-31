/**
 * The messenger controller: owns all app state and side effects (API, realtime,
 * the postMessage bridge to the loader). Components stay presentational and call
 * these actions; `subscribe`/`getState` drive re-renders via the `useController`
 * hook.
 */

import { ApiError, WidgetApi, widgetWsUrl } from '../api'
import { MSG } from '../protocol'
import type {
  ArticleDetail,
  BootContact,
  BootResponse,
  BootWorkspace,
  ConversationSummary,
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
}

export class Controller {
  private state: AppState = INITIAL
  private listeners = new Set<() => void>()
  private params: BootParams
  private api: WidgetApi
  private socket: WidgetSocket | null = null
  private token: string | null = null
  private seenIds = new Set<string>()
  private typingTimer: ReturnType<typeof setTimeout> | null = null
  private agentTypingTimer: ReturnType<typeof setTimeout> | null = null

  constructor(params: BootParams) {
    this.params = params
    this.api = new WidgetApi(params.apiBase)
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

  /** React to loader → app messages (currently: refresh on open). */
  private bindBridge(): void {
    bridge.on((env) => {
      if (env.type === MSG.OPEN) void this.refreshConversations()
    })
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
      return err.message || 'Something went wrong.'
    }
    return 'Unable to reach the chat service.'
  }
}
