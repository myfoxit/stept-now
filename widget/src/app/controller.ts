/**
 * The messenger controller: owns all app state and side effects (API, realtime,
 * the postMessage bridge to the loader). Components stay presentational and call
 * these actions; `subscribe`/`getState` drive re-renders via the `useController`
 * hook.
 */

import {
  ApiError,
  errorCode,
  fetchTours,
  sendMessageFeedback,
  triggerCampaign,
  WidgetApi,
  widgetWsUrl,
} from '../api'
import type { ClientActionWireDef } from '../actions'
import { MSG } from '../protocol'
import type {
  ArticleDetail,
  BootContact,
  BootResponse,
  BootWorkspace,
  ConversationSummary,
  FeedbackRating,
  Identity,
  Tour,
  WidgetArticlesResponse,
  WidgetConfig,
  WidgetMessage,
  RealtimeMessage,
} from '../types'
import { isRequireIdentity } from '../types'
import {
  MSG_EXTRA,
  parseTourState,
  tourEvent,
  type FederatedResults,
  type Starter,
  type TourState,
} from './api-extra'
import { bridge } from './bridge'
import { DEFAULT_LOCALE, ensureCatalog, getLocale, hasCatalog, setLocale, t } from '../i18n'
import { browserLanguages, resolveLocale } from '../i18n/resolve'
import { WidgetSocket } from './ws'

export interface BootParams {
  workspaceKey: string
  apiBase: string
  identity?: Identity
  /** `SteptSettings.locale` — the host page's explicit interface language. */
  locale?: string
  /** `SteptSettings.lockLocale` — honour `locale` even against what the visitor writes. */
  lockLocale?: boolean
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

/** A client action parked behind its confirm card, waiting on the visitor. */
export interface PendingActionCard {
  opId: string
  runId: string
  /** The developer's action name (`invite_teammate`, not `app_…`). */
  name: string
  params: Record<string, unknown>
  /** From the advertised def, for the card copy ('' when it changed away). */
  description: string
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
  /** A client action waiting for the visitor's Run / Not now. */
  pendingAction: PendingActionCard | null
  /** Active interface language. In state so a change re-renders the tree. */
  locale: string
  /** Live tour progress from the loader — rendered as a system line. */
  tourState: TourState | null
  /** Suggested-question chips for an empty conversation. Null until loaded. */
  starters: Starter[] | null
  /** Conversations where the visitor asked for a human this session. */
  humanRequested: Record<string, boolean>
  /** Home federated search (articles + tours). Null when the box is empty. */
  homeSearch: FederatedResults | null
  /** Which screen opened the current article, so Back returns there. */
  articleOrigin: 'help' | 'home'
}

const INITIAL: AppState = {
  screen: { name: 'loading' },
  locale: DEFAULT_LOCALE,
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
  pendingAction: null,
  tourState: null,
  starters: null,
  humanRequested: {},
  homeSearch: null,
  articleOrigin: 'help',
}

/** Hide the "working on the page…" status line after this long without an
 * update — a crashed run must not leave a forever-spinner (defense in depth;
 * the backend sweep is the real fix). */
export const STATUS_STALE_MS = 90_000

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
  /** Hides a stale "working on the page…" line (see STATUS_STALE_MS). */
  private statusTimer: ReturnType<typeof setTimeout> | null = null
  /** opId → the run waiting on it, while the loader executes the op. */
  private pendingOps = new Map<string, { runId: string; op: string }>()
  /** Client-action defs the loader last advertised (null until it has). */
  private actionDefs: ClientActionWireDef[] | null = null
  /** Is the messenger panel visible? Tracked from the loader's OPEN/CLOSE. */
  private panelOpen = false
  /** Live tours for the current page, fetched once (starters + search). */
  private toursCache: { url: string; tours: Tour[] } | null = null

  constructor(params: BootParams) {
    this.params = params
    this.api = new WidgetApi(params.apiBase)
    // Pick a language before the first paint. Boot refines this once the
    // server tells us what this contact actually writes in; doing it here means
    // the loading and error screens are already in the right language, which is
    // exactly when a visitor is least able to cope with a foreign one.
    this.applyLocale()
  }

  /**
   * Re-resolve the interface language and re-render if it changed.
   *
   * `learned` is the language the server detected from this visitor's own
   * messages. It outranks the browser header and the host page's setting
   * because it is the only signal that is evidence about *this person* — see
   * `i18n/resolve.ts`.
   */
  private applyLocale(learned?: string | null): string {
    const next = resolveLocale({
      explicit: this.params.locale,
      lockLocale: this.params.lockLocale,
      learned: learned ?? this.state?.contact?.locale ?? null,
      browser: browserLanguages(),
      workspaceDefault: (this.state?.config?.default_locale as string | undefined) ?? null,
    })
    if (next === getLocale() && hasCatalog(next)) return next
    setLocale(next)
    // `set` is safe before the first render: listeners is empty until subscribe.
    this.set({ locale: next })
    if (!hasCatalog(next)) {
      // Only English is bundled; fetch the rest and re-render when it lands.
      // Until then every string falls back to English rather than a raw key.
      void ensureCatalog(next, this.params.apiBase).then(() => {
        if (getLocale() === next) this.set({ locale: next })
      })
    }
    return next
  }

  /**
   * Adopt a language the server detected for this visitor.
   *
   * Called when a message response reports a `detected_locale` — the moment a
   * German question arrives from an `en-US` browser, the composer placeholder,
   * the buttons, and the timestamps all switch to German too.
   */
  adoptDetectedLocale(locale: string | null | undefined): void {
    if (!locale) return
    const contact = this.state.contact
    if (contact) this.set({ contact: { ...contact, locale } })
    this.applyLocale(locale)
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
    if (this.statusTimer) clearTimeout(this.statusTimer)
    this.statusTimer = null
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
      this.set({ screen: { name: 'error', message: t('error.missing_key') } })
      return
    }
    // Bind before the fetch: an OPEN that arrives while boot is in flight must
    // still be seen, or the unread→thread routing below misses its cue.
    this.bindBridge()
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
    // Now that we know the contact and the workspace default, resolve again.
    this.applyLocale(boot.contact?.locale)
    this.connectRealtime()
    this.emitReady()
    // The panel was opened before boot finished (or was already open on a
    // re-boot): route straight to the one conversation waiting on the visitor.
    if (this.panelOpen) this.maybeOpenUnreadThread()
  }

  /** React to loader → app messages (refresh on open; trigger due campaigns). */
  private bindBridge(): void {
    this.unbindBridge?.()
    this.unbindBridge = bridge.on((env) => {
      if (env.type === MSG.OPEN) {
        const wasOpen = this.panelOpen
        this.panelOpen = true
        void this.refreshConversations().then(() => {
          // Route only on the closed→open transition, so a re-sent OPEN can
          // never yank a visitor out of whatever they navigated to since.
          if (!wasOpen) this.maybeOpenUnreadThread()
        })
      } else if (env.type === MSG.CLOSE) {
        this.panelOpen = false
      } else if (env.type === MSG.TOUR_EVENT || (env.type as string) === MSG_EXTRA.TOUR_STATE) {
        this.onTourState((env.payload || {}) as Record<string, unknown>)
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
    if (Array.isArray(payload.actions)) {
      // Function-free defs from the loader's registry; an older loader build
      // sends nothing, and null keeps the backend's stored set untouched.
      this.actionDefs = payload.actions.filter(
        (d): d is ClientActionWireDef =>
          !!d && typeof d === 'object' && typeof (d as ClientActionWireDef).name === 'string',
      )
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
        clientActions: this.actionDefs ?? undefined,
      })
      this.set({ pageControl: ack.page_control, actionsAllowed: ack.allow_actions })
      this.warnRejectedActions(ack.accepted_actions)
    } catch {
      /* page context is an enhancement — a failure just means less context */
    }
  }

  /** Surface defs the server dropped (bad name, over a cap) — otherwise a
   * developer's action silently never exists and there is nothing to debug. */
  private warnRejectedActions(accepted: string[] | undefined): void {
    if (!this.actionDefs?.length || !Array.isArray(accepted)) return
    const rejected = this.actionDefs.filter((d) => !accepted.includes(d.name)).map((d) => d.name)
    if (rejected.length && typeof console !== 'undefined') {
      console.warn(`[stept] actions not accepted by the server: ${rejected.join(', ')}`)
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
    // The socket frame and the reload re-fetch can deliver the same op; a page
    // op executed twice is wasteful, a client action executed twice is a real
    // double side effect. First delivery wins.
    if (this.pendingOps.has(opId) || this.state.pendingAction?.opId === opId) return
    const args = (data.args || {}) as Record<string, unknown>

    if (op === 'action') {
      const name = typeof args.name === 'string' ? args.name : ''
      const params = (args.params || {}) as Record<string, unknown>
      if (args.confirm !== false) {
        // Park behind the card; the loader hears nothing until Run.
        const def = this.actionDefs?.find((d) => d.name === name)
        this.set({
          pendingAction: { opId, runId, name, params, description: def?.description || '' },
        })
        return
      }
      this.pendingOps.set(opId, { runId, op })
      this.setWorking(op)
      bridge.post(MSG.COPILOT_OP, { opId, op, args: { name, params } })
      return
    }

    this.setWorking(op)

    if (op === 'guide') {
      const tourId = typeof args.tour_id === 'string' ? args.tour_id : ''
      if (!tourId) {
        void this.finishOp(runId, opId, { ok: false, error: 'missing tour_id' })
        return
      }
      // Wait for the loader to report whether the tour actually started. Acking
      // optimistically let the assistant announce "the guide is now playing"
      // when nothing had — the visitor is then told to watch a tour that never
      // appears, and the model has no idea anything went wrong.
      this.pendingOps.set(opId, { runId, op })
      bridge.post(MSG.TOUR_START, { tourId, opId })
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

  /** Run the client action waiting behind its confirm card. */
  confirmPendingAction(): void {
    const card = this.state.pendingAction
    if (!card) return
    this.pendingOps.set(card.opId, { runId: card.runId, op: 'action' })
    this.set({ pendingAction: null })
    this.setWorking('action')
    bridge.post(MSG.COPILOT_OP, {
      opId: card.opId,
      op: 'action',
      args: { name: card.name, params: card.params },
    })
  }

  /** Refuse it. The run resumes with a declined result the model can explain. */
  declinePendingAction(): void {
    const card = this.state.pendingAction
    if (!card) return
    this.set({ pendingAction: null })
    void this.finishOp(card.runId, card.opId, {
      ok: false,
      declined: true,
      error: 'the person chose not to run this action',
    })
  }

  /** The loader answered a page op — pass the result back to the agent run. */
  private onCopilotResult(payload: Record<string, unknown>): void {
    const opId = typeof payload.opId === 'string' ? payload.opId : ''
    const pending = opId ? this.pendingOps.get(opId) : undefined
    if (!pending) return
    this.pendingOps.delete(opId)
    void this.finishOp(pending.runId, opId, (payload.result ?? {}) as Record<string, unknown>)
  }

  /**
   * Set/clear the "working on the page…" status line — always through here, so
   * every update re-arms the staleness timeout. A run that dies without a
   * result (worker crash, dropped socket) then costs the visitor 90 seconds of
   * spinner, not forever.
   */
  private setWorking(op: string | null): void {
    if (this.statusTimer) clearTimeout(this.statusTimer)
    this.statusTimer = null
    if (op !== null) {
      this.statusTimer = setTimeout(() => this.set({ workingOnPage: null }), STATUS_STALE_MS)
    }
    this.set({ workingOnPage: op })
  }

  private async finishOp(runId: string, opId: string, result: unknown): Promise<void> {
    const { screen } = this.state
    const conversationId = screen.name === 'thread' ? screen.conversationId : null
    this.setWorking(null)
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

  /**
   * On open: when exactly one conversation is waiting on the visitor, land in
   * that thread instead of making them hunt for it from Home. With several
   * unread (or none) Home is the right place — its rows carry the badges.
   *
   * Never called while the panel is closed: entering the thread marks it read,
   * which would silently clear a launcher badge nobody has seen.
   */
  private maybeOpenUnreadThread(): void {
    if (this.state.screen.name !== 'home') return
    const unread = this.state.conversations.filter((c) => c.unread)
    if (unread.length === 1) void this.openConversation(unread[0]!.id)
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
      pendingAction: null,
    })
    void this.loadStarters()
  }

  async openConversation(id: string): Promise<void> {
    this.seenIds.clear()
    this.set({
      screen: { name: 'thread', conversationId: id },
      messages: [],
      nextCursor: null,
      loadingMessages: true,
      agentTyping: false,
      pendingAction: null,
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
        const summary = await this.api.createConversation(content, this.actionDefs ?? undefined)
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
        const real = (await this.api.sendMessage(
          screen.conversationId,
          content,
          this.actionDefs ?? undefined,
        )) as UiMessage
        this.replaceTemp(temp.id, real)
        // If this message revealed which language they write in, follow it.
        this.adoptDetectedLocale(real.detected_locale)
      }
    } catch {
      this.markFailed(temp.id)
    }
  }

  /** Re-send a message that failed to deliver: drop the failed bubble and try
   * again with its text (the composer stays clear). */
  async retry(failedId: string): Promise<void> {
    const failed = this.state.messages.find((m) => m.id === failedId && m.failed)
    if (!failed) return
    this.set({ messages: this.state.messages.filter((m) => m.id !== failedId) })
    await this.send(failed.content)
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

  // --- human handoff --------------------------------------------------------

  /**
   * "Talk to a person": sends the request as a regular visitor message (the
   * backend already routes visitor messages to the inbox) and remembers that
   * this conversation is waiting on a human, so the thread can set expectations
   * instead of promising another instant AI answer.
   */
  async requestHuman(): Promise<void> {
    if (this.state.screen.name !== 'thread') return
    await this.send(t('handoff.message'))
    const { screen } = this.state
    // send() creates the conversation when it was a fresh thread.
    const id = screen.name === 'thread' ? screen.conversationId : null
    if (id) this.set({ humanRequested: { ...this.state.humanRequested, [id]: true } })
  }

  // --- tours (offers, live state) ------------------------------------------

  /** Start a stored tour in the host page (tour_offer card, search result). */
  startTour(tourId: string): void {
    if (!tourId) return
    bridge.post(MSG.TOUR_START, { tourId })
    // The loader collapses the panel to its tour-progress pill and restores it
    // when the tour ends — closing here would opt out of that restore.
  }

  /** Ask the loader to pick an interrupted tour back up. */
  resumeTour(tourId: string): void {
    if (!tourId) return
    bridge.post(MSG_EXTRA.TOUR_RESUME, { tourId })
  }

  /** Live tour progress from the loader (tour:state, or legacy TOUR_EVENT). */
  private onTourState(payload: Record<string, unknown>): void {
    const next = parseTourState(payload)
    if (!next) return
    // Keep the last known title/total when a later frame omits them.
    const prev = this.state.tourState
    const merged: TourState =
      prev && prev.tourId === next.tourId
        ? {
            ...next,
            title: next.title || prev.title,
            total: next.total ?? prev.total,
            step: next.step ?? prev.step,
          }
        : next
    this.set({ tourState: merged })
  }

  // --- conversation starters ------------------------------------------------

  /**
   * Suggest 3–4 openers for an empty conversation, sourced from what this
   * workspace can actually deliver: live tours for the visitor's page and top
   * help-center articles. Loaded once per session; an empty result renders as
   * no chips, never an error.
   */
  private async loadStarters(): Promise<void> {
    if (this.state.starters !== null) return
    try {
      const [tours, articles] = await Promise.all([
        this.liveTours(),
        this.state.articles
          ? Promise.resolve(this.state.articles)
          : this.api.getArticles('').catch(() => ({ collections: [], results: [] })),
      ])
      const starters: Starter[] = []
      for (const tour of tours.slice(0, 2)) {
        if (tour.name) starters.push({ kind: 'tour', text: t('starters.tour', { name: tour.name }) })
      }
      const titles = articles.collections.flatMap((c) => c.articles.map((a) => a.title))
      for (const title of titles) {
        if (starters.length >= 4) break
        if (title) starters.push({ kind: 'article', text: title })
      }
      this.set({ starters })
    } catch {
      this.set({ starters: [] })
    }
  }

  /** Live tours for the current page, fetched once and reused (starters +
   * federated search). No page context yet → no tours, silently. */
  private async liveTours(): Promise<Tour[]> {
    const url = this.state.page?.url
    if (!url) return []
    if (this.toursCache?.url === url) return this.toursCache.tours
    try {
      const tours = await fetchTours(this.params.apiBase, this.params.workspaceKey, url, this.token)
      this.toursCache = { url, tours }
      return tours
    } catch {
      return []
    }
  }

  // --- federated search (home) ---------------------------------------------

  /**
   * One query across articles AND live tours, grouped for the Home screen.
   * Tours have no search endpoint, so the cached page-eligible list is
   * filtered by name here.
   */
  async searchEverything(query: string): Promise<void> {
    const q = query.trim()
    if (!q) {
      this.set({ homeSearch: null })
      return
    }
    this.set({
      homeSearch: { query: q, loading: true, articles: [], tours: [] },
    })
    const [articles, tours] = await Promise.all([
      this.api.getArticles(q).catch(() => ({ collections: [], results: [] })),
      this.liveTours(),
    ])
    // A slower response for an older query must not clobber the current one.
    if (this.state.homeSearch?.query !== q) return
    const needle = q.toLowerCase()
    this.set({
      homeSearch: {
        query: q,
        loading: false,
        articles: articles.results.map((r) => ({
          title: r.title,
          slug: r.slug,
          snippet: r.snippet,
        })),
        tours: tours
          .filter((tour) => tour.name.toLowerCase().includes(needle))
          .map((tour) => ({ id: tour.id, name: tour.name, steps: tour.steps.length })),
      },
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
      this.set({
        messages: [...this.state.messages, message],
        agentTyping: false,
        // A reply while a confirm card is up means the run moved on without the
        // answer (declined elsewhere, or the timeout sweep) — the card is dead.
        ...(raw.direction === 'out' && this.state.pendingAction ? { pendingAction: null } : {}),
      })
      if (raw.direction === 'out') void this.markRead(convId)
    } else if (convId && raw.direction === 'out') {
      // Tour telemetry lines are ambient, not replies: they must not become the
      // row's preview ("✕ Dismissed at step 2" as the teaser) nor ping unread.
      if (tourEvent(raw)) return
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

  async openArticle(slug: string, origin: 'help' | 'home' = 'help'): Promise<void> {
    this.set({
      screen: { name: 'article', slug },
      articleOrigin: origin,
      article: null,
      loadingArticle: true,
    })
    try {
      const article = await this.api.getArticle(slug)
      this.set({ article, loadingArticle: false })
    } catch (err) {
      this.set({ loadingArticle: false, screen: { name: 'error', message: this.describe(err) } })
    }
  }

  /** Back from an article: to the help browser, or Home when the federated
   * search opened it — never to a screen the visitor was not on. */
  backFromArticle(): void {
    if (this.state.articleOrigin === 'home') {
      this.set({ screen: { name: 'home' }, article: null })
    } else {
      this.backToHelp()
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
    this.panelOpen = false
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
      if (err.status === 404) return t('error.widget_unavailable')
      // A blocked visitor gets the same neutral wording as an unavailable
      // widget: telling them they're blocked is hostile and confirms the block.
      if (errorCode(err) === 'contact_blocked') return t('error.unavailable')
      return err.message || t('error.generic')
    }
    return t('error.unreachable')
  }
}
