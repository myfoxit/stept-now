/**
 * Stept embeddable loader (host-page script → dist/loader.js, a tiny IIFE).
 *
 * Responsibilities:
 *  - read `window.SteptSettings` ({workspaceKey, identity?, apiBase?});
 *  - render a launcher bubble and an iframe host for the Preact messenger
 *    (served from `{apiBase}/widget-assets/app.html`);
 *  - bridge the host page and the iframe over postMessage;
 *  - expose the `window.Stept(cmd, …)` queue API;
 *  - run the DAP engine: one `GET /api/widget/experiences` bootstrap per page
 *    view (re-evaluated on SPA URL changes) delivering tours, checklists and
 *    surveys, played in the host DOM with telemetry reported back;
 *  - run the proactive-campaign engine: evaluate ongoing campaigns against the
 *    current URL + time-on-page and nudge the iframe (which holds the visitor
 *    token) to trigger at most one per page view — badge only, never auto-open.
 *
 * Experience priority per page view: an active tour wins, then a survey, then
 * the checklist auto-open. Never two overlays at once; the checklist launcher
 * pill coexists with the messenger launcher.
 *
 * No framework, no external deps. Kept small; the testable pieces live in
 * loader-core.ts, dom-target.ts, tour-player.ts, checklist-widget.ts,
 * survey-widget.ts and api.ts.
 */

import {
  fetchCampaigns,
  fetchExperiences,
  fetchPreviewTour,
  fetchTour,
  postChecklistDismiss,
  postChecklistProgress,
  postSurveyResponse,
  postTourEvent,
} from './api'
import { ChecklistWidget } from './checklist-widget'
import {
  campaignDelayMs,
  campaignSeenKey,
  firstDueCampaign,
  installStept,
  parsePreviewHash,
  patchHistory,
  pruneSeenCampaigns,
  readSeenSet,
  restoreHistory,
  selectEligibleCampaigns,
  writeSeenSet,
  type SteptCommandHandlers,
  type SteptFn,
} from './loader-core'
import { ActionRegistry } from './actions'
import { PageAgent, type PageOp } from './page-agent'
import { envelope, MSG, type MessageType, parseEnvelope } from './protocol'
import { selectFirstEligibleSurvey, SurveyWidget, surveySeenKey } from './survey-widget'
import { selectFirstEligibleTour, TourPlayer, tourProgressKey } from './tour-player'
import type {
  Campaign,
  Checklist,
  ChecklistItem,
  ExperiencesResponse,
  SteptSettings,
  Survey,
  SurveyAnswer,
  Tour,
  TourEventMeta,
  TourEventName,
  TourStep,
} from './types'
import { ensureCatalog, setLocale, t } from './i18n'
import { browserLanguages, resolveLocale } from './i18n/resolve'

const currentScript = document.currentScript as HTMLScriptElement | null

/** Origin the loader was served from — the default API/asset base. */
function scriptOrigin(): string {
  try {
    if (currentScript?.src) return new URL(currentScript.src).origin
  } catch {
    /* ignore */
  }
  return window.location.origin
}

const LAUNCHER_ID = 'stept-launcher'
const FRAME_ID = 'stept-frame'
const STYLE_ID = 'stept-loader-style'

const CSS = `
#${LAUNCHER_ID}{position:fixed;bottom:20px;z-index:2147482900;width:60px;height:60px;
  border-radius:50%;border:0;cursor:pointer;box-shadow:0 6px 20px rgba(15,23,42,.28);
  background:var(--stept-accent,#5b46e5);color:#fff;display:flex;align-items:center;
  justify-content:center;transition:transform .15s ease,opacity .15s ease}
#${LAUNCHER_ID}:hover{transform:scale(1.05)}
#${LAUNCHER_ID}.stept-right{right:20px}
#${LAUNCHER_ID}.stept-left{left:20px}
#${LAUNCHER_ID} svg{width:28px;height:28px;display:block}
#${LAUNCHER_ID} .stept-badge{position:absolute;top:-2px;right:-2px;min-width:20px;height:20px;
  padding:0 5px;border-radius:10px;background:#ef4444;color:#fff;font:600 11px/20px system-ui,sans-serif;
  text-align:center;box-sizing:border-box;box-shadow:0 0 0 2px #fff}
#${FRAME_ID}{position:fixed;bottom:92px;z-index:2147482901;width:400px;height:640px;
  max-height:calc(100vh - 112px);border:0;border-radius:16px;overflow:hidden;
  box-shadow:0 12px 48px rgba(15,23,42,.32);background:transparent;opacity:0;
  transform:translateY(12px);pointer-events:none;transition:opacity .2s ease,transform .2s ease}
#${FRAME_ID}.stept-right{right:20px}
#${FRAME_ID}.stept-left{left:20px}
#${FRAME_ID}.stept-open{opacity:1;transform:translateY(0);pointer-events:auto}
@media (max-width:480px){
  #${FRAME_ID}{inset:0;width:100%;height:100%;max-height:100%;border-radius:0;bottom:0}
  #${FRAME_ID}.stept-open ~ #${LAUNCHER_ID},#${LAUNCHER_ID}.stept-hidden-mobile{opacity:0}
}
#${LAUNCHER_ID}.stept-gone{display:none}
`

const CHAT_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>'
const CLOSE_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'

class WidgetHost {
  private settings: SteptSettings
  private apiBase: string
  private widgetKey: string
  private position: 'left' | 'right' = 'right'
  private accent = '#5b46e5'

  private launcher: HTMLButtonElement | null = null
  private badge: HTMLSpanElement | null = null
  private frame: HTMLIFrameElement | null = null
  private open = false
  private token: string | null = null

  private tourPlayer: TourPlayer | null = null
  private checklist: ChecklistWidget | null = null
  private survey: SurveyWidget | null = null
  private lastExperienceUrl = ''
  private previewing = false

  /** Ongoing campaigns, fetched once per page session (reset on re-boot). */
  private campaigns: Campaign[] | null = null
  private campaignTimer: number | null = null
  private lastCampaignUrl = ''

  /** Host-page executor for AI copilot ops — built on first use. */
  private pageAgent: PageAgent | null = null

  /** Client actions the page registered (`Stept('action', …)`). Survives
   * shutdown/boot cycles: the handlers belong to the page, not this instance. */
  private actions = new ActionRegistry()

  constructor(settings: SteptSettings) {
    this.settings = settings
    this.apiBase = (settings.apiBase || scriptOrigin()).replace(/\/+$/, '')
    this.widgetKey = settings.workspaceKey || settings.widgetKey || ''
    this.applyLocale()
  }

  /**
   * The launcher, tours, checklists and surveys render in the host page, in a
   * different JS context from the iframe app, so this bundle keeps its own copy
   * of the active locale. There is no contact here — only the host's setting
   * and the browser — so it resolves once and stays put; the iframe re-resolves
   * with what the visitor writes and pushes any change over the bridge.
   */
  private applyLocale(): void {
    const code = setLocale(
      resolveLocale({
        explicit: this.settings.locale,
        lockLocale: this.settings.lockLocale,
        browser: browserLanguages(),
      }),
    )
    // Only English ships inside the bundle; anything else is a small fetch.
    // The launcher renders in English for that instant and relabels itself
    // when the catalog lands — a visible-but-correct label beats blocking the
    // host page's first paint on our network call.
    void ensureCatalog(code, this.apiBase).then(() => this.relabel())
  }

  /** Re-apply translated labels to host-page chrome after a late catalog load. */
  private relabel(): void {
    if (!this.launcher) return
    this.launcher.setAttribute('aria-label', this.open ? t('launcher.close') : t('launcher.open'))
  }

  init(): void {
    if (!this.widgetKey) {
      console.error('[stept] missing workspaceKey in window.SteptSettings')
      return
    }
    this.buildEngines()
    this.injectStyle()
    this.renderLauncher()
    this.renderFrame()
    window.addEventListener('message', this.onMessage)
    this.watchUrlChanges()
    void this.checkPreview()
  }

  // --- DAP engines ---------------------------------------------------------

  private buildEngines(): void {
    this.tourPlayer = new TourPlayer({
      accent: this.accent,
      apiBase: this.apiBase,
      progressKey: tourProgressKey(this.widgetKey),
      onEvent: (event, stepIndex, meta) => this.onTourEvent(event, stepIndex, meta),
    })
    this.checklist = new ChecklistWidget({
      widgetKey: this.widgetKey,
      apiBase: this.apiBase,
      onAction: (item, checklist) => this.runChecklistAction(item, checklist),
      onProgress: (checklistId, itemId, done) => {
        postChecklistProgress(
          this.apiBase,
          this.widgetKey,
          checklistId,
          itemId,
          done,
          this.token,
        ).catch(() => {
          /* anonymous / offline — the widget keeps the state locally */
        })
      },
      onDismiss: (checklistId) => {
        postChecklistDismiss(this.apiBase, this.widgetKey, checklistId, this.token).catch(() => {})
      },
    })
    this.survey = new SurveyWidget({
      widgetKey: this.widgetKey,
      onSubmit: (surveyId, answers, completed) => this.submitSurvey(surveyId, answers, completed),
    })
  }

  /** True while a tour, survey or the checklist panel owns the screen. */
  private hasOverlay(): boolean {
    return Boolean(this.tourPlayer?.active || this.survey?.active || this.checklist?.isOpen)
  }

  // --- DOM -----------------------------------------------------------------

  private injectStyle(): void {
    if (document.getElementById(STYLE_ID)) return
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    document.head.appendChild(style)
  }

  private renderLauncher(): void {
    const btn = document.createElement('button')
    btn.id = LAUNCHER_ID
    btn.className = `stept-${this.position}`
    btn.setAttribute('aria-label', t('launcher.open'))
    btn.style.setProperty('--stept-accent', this.accent)
    btn.innerHTML = CHAT_ICON
    const badge = document.createElement('span')
    badge.className = 'stept-badge'
    badge.style.display = 'none'
    btn.appendChild(badge)
    btn.addEventListener('click', () => this.toggle())
    document.body.appendChild(btn)
    this.launcher = btn
    this.badge = badge
  }

  private renderFrame(): void {
    const frame = document.createElement('iframe')
    frame.id = FRAME_ID
    frame.className = `stept-${this.position}`
    frame.title = t('launcher.title')
    frame.allow = 'clipboard-write'
    frame.src = this.frameSrc()
    document.body.appendChild(frame)
    this.frame = frame
  }

  private frameSrc(): string {
    const params = {
      workspaceKey: this.widgetKey,
      apiBase: this.apiBase,
      identity: this.settings.identity,
      locale: this.settings.locale,
      lockLocale: this.settings.lockLocale,
    }
    const hash = encodeURIComponent(JSON.stringify(params))
    return `${this.apiBase}/widget-assets/app.html#${hash}`
  }

  // --- open / close / visibility ------------------------------------------

  toggle(): void {
    if (this.open) this.close()
    else this.openPanel()
  }

  openPanel(): void {
    this.open = true
    this.frame?.classList.add('stept-open')
    this.launcher?.setAttribute('aria-label', t('launcher.close'))
    if (this.launcher) this.launcher.innerHTML = CLOSE_ICON
    this.reattachBadge()
    this.post(MSG.OPEN, {})
  }

  close(): void {
    this.open = false
    this.frame?.classList.remove('stept-open')
    this.launcher?.setAttribute('aria-label', t('launcher.open'))
    if (this.launcher) this.launcher.innerHTML = CHAT_ICON
    this.reattachBadge()
    this.post(MSG.CLOSE, {})
  }

  show(): void {
    this.launcher?.classList.remove('stept-gone')
  }

  hide(): void {
    this.launcher?.classList.add('stept-gone')
  }

  shutdown(): void {
    window.removeEventListener('message', this.onMessage)
    window.removeEventListener('popstate', this.onLocationChange)
    window.removeEventListener('stept:locationchange', this.onLocationChange)
    restoreHistory(window)
    this.clearCampaignTimer()
    this.campaigns = null
    this.lastCampaignUrl = ''
    this.lastExperienceUrl = ''
    this.previewing = false
    this.tourPlayer?.stop()
    this.checklist?.unmount()
    this.survey?.close(false)
    this.tourPlayer = this.checklist = this.survey = null
    this.activeTourId = null
    this.launcher?.remove()
    this.frame?.remove()
    this.launcher = this.frame = this.badge = null
    this.open = false
    this.token = null
  }

  boot(settings?: SteptSettings): void {
    // Re-boot with merged settings (e.g. after login sets identity).
    this.shutdown()
    this.settings = { ...this.settings, ...(settings || {}) }
    this.apiBase = (this.settings.apiBase || scriptOrigin()).replace(/\/+$/, '')
    this.widgetKey = this.settings.workspaceKey || this.settings.widgetKey || ''
    this.init()
  }

  private reattachBadge(): void {
    if (this.launcher && this.badge && !this.badge.isConnected) {
      this.launcher.appendChild(this.badge)
    }
  }

  private setUnread(count: number): void {
    if (!this.badge) return
    if (count > 0 && !this.open) {
      this.badge.textContent = count > 9 ? '9+' : String(count)
      this.badge.style.display = ''
    } else {
      this.badge.style.display = 'none'
    }
  }

  // --- postMessage bridge --------------------------------------------------

  private onMessage = (event: MessageEvent): void => {
    if (this.frame && event.source !== this.frame.contentWindow) return
    const env = parseEnvelope(event.data)
    if (!env) return
    const payload = (env.payload || {}) as Record<string, unknown>
    switch (env.type) {
      case MSG.READY:
        this.token = (payload.token as string) || null
        if (payload.accent) this.applyAccent(String(payload.accent))
        if (payload.position === 'left' || payload.position === 'right') {
          this.applyPosition(payload.position)
        }
        this.setUnread(Number(payload.unread ?? 0))
        this.pushPageContext()
        void this.checkExperiences(true)
        void this.checkCampaigns(true)
        break
      case MSG.UNREAD:
        this.setUnread(Number(payload.count ?? 0))
        break
      case MSG.OPEN:
        this.openPanel()
        break
      case MSG.CLOSE:
        this.close()
        break
      case MSG.RESIZE:
        if (this.frame && typeof payload.height === 'number') {
          this.frame.style.height = `${payload.height}px`
        }
        break
      case MSG.TOUR_START:
        void this.startTour(String(payload.tourId ?? ''))
        break
      case MSG.COPILOT_OP:
        void this.runCopilotOp(payload)
        break
      case MSG.GUIDE_START:
        this.playAdHocGuide(payload)
        break
    }
  }

  // --- AI copilot: host-page ops + ad-hoc guides ---------------------------

  /**
   * Execute one AI page op in the host DOM and answer with the result.
   *
   * The iframe app holds the visitor token and the conversation; only the loader
   * can touch the host document, so every copilot action round-trips here. The
   * result is posted back keyed by `opId` — never thrown, so a failed op becomes
   * a tool error the model can read and adapt to.
   */
  private async runCopilotOp(payload: Record<string, unknown>): Promise<void> {
    const opId = String(payload.opId ?? '')
    if (!opId) return
    const op = payload.op as PageOp['op'] | 'action' | undefined
    if (!op) {
      this.post(MSG.COPILOT_RESULT, { opId, result: { ok: false, error: 'missing op' } })
      return
    }
    if (op === 'action') {
      // A registered client action — the developer's own code, never the DOM
      // executor. The confirm gate already happened in the iframe.
      const args = (payload.args ?? {}) as Record<string, unknown>
      const result = await this.actions.execute(
        String(args.name ?? ''),
        (args.params ?? {}) as Record<string, unknown>,
      )
      this.post(MSG.COPILOT_RESULT, { opId, result })
      return
    }
    const result = await this.agent().run({
      op,
      args: (payload.args as PageOp['args']) ?? {},
    })
    this.post(MSG.COPILOT_RESULT, { opId, result })
  }

  /**
   * The host-page executor, built on first use.
   *
   * One instance for the page's lifetime: it carries the index → element binding
   * from the last snapshot, which is what lets an action (or an AI-authored guide
   * step) re-find its target after the page re-rendered.
   */
  private agent(): PageAgent {
    if (!this.pageAgent) {
      this.pageAgent = new PageAgent({ allowedOrigins: this.settings.aiAllowedOrigins })
    }
    return this.pageAgent
  }

  /**
   * Play an AI-authored guide: the assistant's steps, the stored-tour overlay.
   *
   * Wrapped in a synthetic `Tour` so the coach-mark rendering, self-healing
   * target resolution and progress persistence are literally the same code path
   * a recorded tour uses. Telemetry stays local (`activeTourId` unset) because
   * there is no tour row to attribute events to.
   *
   * The model addresses elements by the `[index]` it saw in a snapshot, which is
   * only valid until the next one. Each index is resolved to a durable `Target`
   * here, at start time, so the walkthrough survives the re-renders the visitor's
   * own clicks cause as they step through it.
   */
  private playAdHocGuide(payload: Record<string, unknown>): void {
    const steps = this.resolveGuideSteps(payload.steps)
    if (!steps.length) return
    this.survey?.close(false)
    this.checklist?.closePanel()
    this.activeTourId = null
    this.tourPlayer?.start(
      {
        id: `ai-${Date.now()}`,
        name: String(payload.name ?? t('tour.how_to')),
        steps,
        theme: { accent: this.accent },
        version: 1,
        settings: {
          mode: 'guided',
          backdrop: false,
          show_progress: steps.length > 1,
          dismissable: true,
        },
      },
      { preview: true },
    )
  }

  /**
   * Turn the model's `{index?, title, body?}` steps into playable TourSteps.
   *
   * A step whose index no longer resolves becomes a centred instruction card
   * rather than being dropped: losing step 2 of 4 would leave a walkthrough that
   * silently skips the important click.
   */
  private resolveGuideSteps(raw: unknown): TourStep[] {
    if (!Array.isArray(raw)) return []
    const steps: TourStep[] = []
    for (const [position, item] of raw.entries()) {
      if (!item || typeof item !== 'object') continue
      const entry = item as { index?: unknown; title?: unknown; body?: unknown }
      const title = String(entry.title ?? '').trim()
      if (!title) continue
      const anchored =
        typeof entry.index === 'number' ? this.agent().describe(entry.index) : null
      steps.push({
        id: `ai-step-${position}`,
        type: anchored ? 'tooltip' : 'modal',
        selector: anchored?.selector ?? '',
        fallback_selectors: anchored?.fallback_selectors ?? [],
        text_hint: anchored?.text_hint ?? '',
        target: anchored?.target ?? null,
        title,
        body: String(entry.body ?? ''),
        placement: 'auto',
      })
    }
    return steps
  }

  /** Tell the app where the visitor is, so the assistant has page context. */
  private pushPageContext(): void {
    this.post(MSG.PAGE_CONTEXT, {
      url: window.location.href,
      path: window.location.pathname,
      title: document.title,
      // Function-free defs; the app forwards them with context/messages so the
      // agent's next turn knows this page's verbs.
      actions: this.actions.wireDefs(),
    })
  }

  /** `Stept('action', def)` — register (or replace) a client action. */
  registerAction(def: unknown): void {
    if (this.actions.register(def)) this.pushPageContext()
  }

  /** `Stept('removeAction', name)` — e.g. a component unmounted. */
  removeAction(name: string): void {
    if (this.actions.remove(name)) this.pushPageContext()
  }

  private post(type: MessageType, payload: unknown): void {
    this.frame?.contentWindow?.postMessage(envelope(type, payload), '*')
  }

  private applyAccent(accent: string): void {
    this.accent = accent
    this.launcher?.style.setProperty('--stept-accent', accent)
  }

  private applyPosition(position: 'left' | 'right'): void {
    this.position = position
    for (const node of [this.launcher, this.frame]) {
      node?.classList.remove('stept-left', 'stept-right')
      node?.classList.add(`stept-${position}`)
    }
  }

  // --- experiences ---------------------------------------------------------

  private activeTourId: string | null = null

  private onLocationChange = (): void => {
    this.pushPageContext()
    void this.checkExperiences(false)
    void this.checkCampaigns(false)
  }

  private watchUrlChanges(): void {
    // Idempotent: a re-boot must not stack another wrapper on history.
    patchHistory(window)
    window.addEventListener('popstate', this.onLocationChange)
    window.addEventListener('stept:locationchange', this.onLocationChange)
  }

  /** One bootstrap per page view: tours + checklists + surveys in one call. */
  private async checkExperiences(force: boolean): Promise<void> {
    const url = window.location.href
    if (!force && url === this.lastExperienceUrl) return
    this.lastExperienceUrl = url
    // `url_visited` checklist items tick on every navigation, even while an
    // overlay is up and even before the next bootstrap lands.
    this.checklist?.checkUrl(url)
    if (this.previewing) return
    try {
      const data = await fetchExperiences(this.apiBase, this.widgetKey, url, this.token)
      this.applyExperiences(data, url)
    } catch (err) {
      console.warn('[stept] experiences check failed', err)
    }
  }

  private applyExperiences(data: ExperiencesResponse, url: string): void {
    let claimed = this.hasOverlay()
    if (!claimed) {
      const tour = selectFirstEligibleTour(data.tours ?? [], this.seenIds(this.toursSeenKey()))
      if (tour) {
        this.play(tour)
        claimed = true
      }
    }
    if (!claimed) {
      const survey = selectFirstEligibleSurvey(
        data.surveys ?? [],
        this.seenIds(surveySeenKey(this.widgetKey)),
      )
      if (survey) {
        this.mountSurvey(survey)
        claimed = true
      }
    }
    const checklist = data.checklists?.[0] ?? null
    if (!checklist) {
      this.checklist?.unmount()
      return
    }
    this.checklist?.mount(checklist, { autoOpen: !claimed })
    this.checklist?.checkUrl(url)
  }

  // --- tours ---------------------------------------------------------------

  /** `Stept('startTour', id)`: fetch THAT tour (manual triggers included). */
  async startTour(tourId: string): Promise<void> {
    if (!tourId) return
    try {
      const tour = await fetchTour(this.apiBase, this.widgetKey, tourId, this.token)
      this.play(tour)
    } catch (err) {
      console.warn(`[stept] startTour ${tourId} failed`, err)
    }
  }

  /** Dashboard preview link: `…#stept-preview=<token>` plays it immediately. */
  private async checkPreview(): Promise<void> {
    const request = parsePreviewHash(window.location.hash)
    if (!request) return
    this.previewing = true
    try {
      const tour = await fetchPreviewTour(this.apiBase, request.tourId, request.token)
      this.play(tour, true)
    } catch (err) {
      this.previewing = false
      console.warn('[stept] tour preview failed', err)
    }
  }

  /** Begin a tour, tagging telemetry with its id (set before 'started' fires). */
  private play(tour: Tour, preview = false): void {
    // One overlay at a time: a tour takes the screen from a survey/checklist.
    this.survey?.close(false)
    this.checklist?.closePanel()
    this.activeTourId = tour.id
    this.tourPlayer?.start(tour, { preview })
  }

  private onTourEvent(
    event: TourEventName,
    stepIndex: number | null,
    meta?: TourEventMeta,
  ): void {
    const tourId = this.activeTourId
    if (!tourId) return
    postTourEvent(
      this.apiBase,
      this.widgetKey,
      tourId,
      event,
      stepIndex,
      this.token,
      meta ?? null,
    ).catch(() => {})
    this.post(MSG.TOUR_EVENT, { tourId, event, stepIndex, meta })
    if (event === 'completed') {
      // Optimistic locally; the server does it authoritatively for contacts.
      this.checklist?.onTourCompleted(tourId)
    }
    if (event === 'completed' || event === 'dismissed') {
      if (this.previewing) {
        // Preview over: let the normal bootstrap run again on this same URL.
        this.previewing = false
        this.lastExperienceUrl = ''
      } else {
        this.rememberSeen(this.toursSeenKey(), tourId)
      }
      this.activeTourId = null
    }
  }

  // --- checklists + surveys ------------------------------------------------

  private runChecklistAction(item: ChecklistItem, checklist: Checklist): void {
    const action = item.action
    if (!action) return
    if (action.type === 'start_tour' && action.tour_id) {
      this.checklist?.closePanel()
      void this.startTour(action.tour_id)
      return
    }
    if (action.type === 'open_url' && action.url) {
      window.open(action.url, '_blank', 'noopener')
      return
    }
    if (action.type === 'open_messenger') {
      this.checklist?.closePanel()
      this.openPanel()
      this.post(MSG.CHECKLIST_ACTION, { checklistId: checklist.id, itemId: item.id })
    }
  }

  private mountSurvey(survey: Survey): void {
    this.rememberSeen(surveySeenKey(this.widgetKey), survey.id)
    this.survey?.mount(survey)
  }

  private submitSurvey(surveyId: string, answers: SurveyAnswer[], completed: boolean): void {
    postSurveyResponse(
      this.apiBase,
      this.widgetKey,
      surveyId,
      answers,
      completed,
      this.token,
      window.location.href,
    ).catch(() => {})
  }

  // --- local seen-sets -----------------------------------------------------

  private toursSeenKey(): string {
    return `stept:tours-seen:${this.widgetKey}`
  }

  private seenIds(key: string): Set<string> {
    return readSeenSet(this.storage(), key)
  }

  private rememberSeen(key: string, id: string): void {
    const seen = this.seenIds(key)
    seen.add(id)
    writeSeenSet(this.storage(), key, seen)
  }

  // --- proactive campaigns -------------------------------------------------

  /**
   * Mirror of the experiences check: on READY and on SPA URL changes, evaluate
   * the ongoing campaigns against the current URL and arm ONE timer for the
   * first due campaign. Firing marks it seen (optimistically) and asks the
   * iframe — which holds the visitor token — to POST the trigger. Never opens
   * the panel.
   */
  private async checkCampaigns(force: boolean): Promise<void> {
    const url = window.location.href
    if (!force && url === this.lastCampaignUrl) return
    this.lastCampaignUrl = url
    this.clearCampaignTimer()
    if (!this.token) return // wait for READY — the iframe does the authed trigger
    try {
      if (!this.campaigns) {
        this.campaigns = await fetchCampaigns(this.apiBase, this.widgetKey)
      }
    } catch (err) {
      console.warn('[stept] campaign check failed', err)
      return
    }
    const campaigns = this.campaigns
    if (!campaigns?.length) return
    const storage = this.storage()
    const key = campaignSeenKey(this.widgetKey)
    const seen = pruneSeenCampaigns(readSeenSet(storage, key), campaigns)
    writeSeenSet(storage, key, seen)
    const due = firstDueCampaign(selectEligibleCampaigns(campaigns, url, seen))
    if (!due) return
    this.campaignTimer = window.setTimeout(() => this.fireCampaign(due), campaignDelayMs(due))
  }

  private fireCampaign(campaign: Campaign): void {
    this.campaignTimer = null
    // Mark seen before the trigger round-trips so it can never fire twice
    // locally; skipped/error outcomes intentionally stay seen too.
    const storage = this.storage()
    const key = campaignSeenKey(this.widgetKey)
    const seen = readSeenSet(storage, key)
    seen.add(campaign.id)
    writeSeenSet(storage, key, seen)
    this.post(MSG.CAMPAIGN_DUE, { campaignId: campaign.id })
  }

  private clearCampaignTimer(): void {
    if (this.campaignTimer !== null) {
      window.clearTimeout(this.campaignTimer)
      this.campaignTimer = null
    }
  }

  private storage(): Storage | null {
    try {
      return window.localStorage
    } catch {
      return null
    }
  }
}

function boot(): void {
  const win = window as unknown as {
    SteptSettings?: SteptSettings
    Stept?: SteptFn
  }
  const host = new WidgetHost(win.SteptSettings || {})

  const handlers: SteptCommandHandlers = {
    boot: (s) => host.boot(s),
    open: () => host.openPanel(),
    close: () => host.close(),
    toggle: () => host.toggle(),
    show: () => host.show(),
    hide: () => host.hide(),
    shutdown: () => host.shutdown(),
    startTour: (id) => void host.startTour(id),
    action: (def) => host.registerAction(def),
    removeAction: (name) => host.removeAction(name),
  }
  installStept(win, handlers)
  host.init()
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot)
} else {
  boot()
}
