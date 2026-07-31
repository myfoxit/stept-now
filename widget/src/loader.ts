/**
 * Stept embeddable loader (host-page script → dist/loader.js, a tiny IIFE).
 *
 * Responsibilities:
 *  - read `window.SteptSettings` ({workspaceKey, identity?, apiBase?});
 *  - render a launcher bubble and an iframe host for the Preact messenger
 *    (served from `{apiBase}/widget-assets/app.html`);
 *  - bridge the host page and the iframe over postMessage;
 *  - expose the `window.Stept(cmd, …)` queue API;
 *  - run the host-DOM product-tour player: on boot and on SPA URL changes it
 *    fetches eligible tours and auto-starts the first one, reporting telemetry.
 *
 * No framework, no external deps. Kept small; the testable pieces live in
 * loader-core.ts, tour-player.ts and api.ts.
 */

import { fetchTours, postTourEvent } from './api'
import { installStept, type SteptCommandHandlers, type SteptFn } from './loader-core'
import { envelope, MSG, type MessageType, parseEnvelope } from './protocol'
import { selectFirstEligibleTour, TourPlayer } from './tour-player'
import type { SteptSettings, Tour, TourEventName } from './types'

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
  background:var(--stept-accent,#6366f1);color:#fff;display:flex;align-items:center;
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
  private accent = '#6366f1'

  private launcher: HTMLButtonElement | null = null
  private badge: HTMLSpanElement | null = null
  private frame: HTMLIFrameElement | null = null
  private open = false
  private token: string | null = null

  private tourPlayer: TourPlayer
  private lastTourUrl = ''

  constructor(settings: SteptSettings) {
    this.settings = settings
    this.apiBase = (settings.apiBase || scriptOrigin()).replace(/\/+$/, '')
    this.widgetKey = settings.workspaceKey || settings.widgetKey || ''
    this.tourPlayer = new TourPlayer({
      accent: this.accent,
      onEvent: (event, stepIndex) => this.onTourEvent(event, stepIndex),
    })
  }

  init(): void {
    if (!this.widgetKey) {
      console.error('[stept] missing workspaceKey in window.SteptSettings')
      return
    }
    this.injectStyle()
    this.renderLauncher()
    this.renderFrame()
    window.addEventListener('message', this.onMessage)
    this.watchUrlChanges()
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
    btn.setAttribute('aria-label', 'Open chat')
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
    frame.title = 'Stept messenger'
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
    this.launcher?.setAttribute('aria-label', 'Close chat')
    if (this.launcher) this.launcher.innerHTML = CLOSE_ICON
    this.reattachBadge()
    this.post(MSG.OPEN, {})
  }

  close(): void {
    this.open = false
    this.frame?.classList.remove('stept-open')
    this.launcher?.setAttribute('aria-label', 'Open chat')
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
        this.checkTours(true)
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
        this.startTour(String(payload.tourId ?? ''))
        break
    }
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

  // --- tours ---------------------------------------------------------------

  private activeTourId: string | null = null

  private watchUrlChanges(): void {
    const fire = () => void this.checkTours(false)
    for (const method of ['pushState', 'replaceState'] as const) {
      const original = history[method]
      history[method] = function (this: History, ...args: Parameters<History['pushState']>) {
        const result = original.apply(this, args)
        window.dispatchEvent(new Event('stept:locationchange'))
        return result
      }
    }
    window.addEventListener('popstate', fire)
    window.addEventListener('stept:locationchange', fire)
  }

  private async checkTours(force: boolean): Promise<void> {
    const url = window.location.href
    if (!force && url === this.lastTourUrl) return
    this.lastTourUrl = url
    if (this.tourPlayer.active) return
    try {
      const tours = await fetchTours(this.apiBase, this.widgetKey, url, this.token)
      const tour = selectFirstEligibleTour(tours, this.seenTours())
      if (tour) this.play(tour)
    } catch (err) {
      console.warn('[stept] tour check failed', err)
    }
  }

  async startTour(tourId: string): Promise<void> {
    if (!tourId) return
    try {
      const tours = await fetchTours(this.apiBase, this.widgetKey, window.location.href, this.token)
      const tour = tours.find((t: Tour) => t.id === tourId)
      if (tour) this.play(tour)
      else console.warn(`[stept] tour ${tourId} not eligible on this page`)
    } catch (err) {
      console.warn('[stept] startTour failed', err)
    }
  }

  /** Begin a tour, tagging telemetry with its id (set before 'started' fires). */
  private play(tour: Tour): void {
    this.activeTourId = tour.id
    this.tourPlayer.start(tour)
  }

  private onTourEvent(event: TourEventName, stepIndex: number | null): void {
    const tourId = this.activeTourId
    if (!tourId) return
    postTourEvent(this.apiBase, this.widgetKey, tourId, event, stepIndex, this.token).catch(() => {})
    this.post(MSG.TOUR_EVENT, { tourId, event, stepIndex })
    if (event === 'completed' || event === 'dismissed') {
      this.rememberSeen(tourId)
      this.activeTourId = null
    }
  }

  private seenTours(): Set<string> {
    try {
      const raw = window.localStorage.getItem(this.seenKey())
      return new Set(raw ? (JSON.parse(raw) as string[]) : [])
    } catch {
      return new Set()
    }
  }

  private rememberSeen(id: string): void {
    try {
      const seen = this.seenTours()
      seen.add(id)
      window.localStorage.setItem(this.seenKey(), JSON.stringify([...seen]))
    } catch {
      /* ignore */
    }
  }

  private seenKey(): string {
    return `stept:tours-seen:${this.widgetKey}`
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
  }
  installStept(win, handlers)
  host.init()
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot)
} else {
  boot()
}
