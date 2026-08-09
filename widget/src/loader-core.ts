/**
 * Loader logic that is kept free of any DOM/iframe wiring so it can be
 * unit-tested in isolation:
 *
 *  - the `window.Stept(command, ...args)` command API and its queue-replay shim
 *    (the loader supplies concrete handlers; this module only routes commands
 *    and drains calls the host queued before loader.js loaded, Intercom-style);
 *  - the proactive-campaign decision logic (URL glob matching, eligibility
 *    filtering, first-due selection, seen-set pruning + localStorage codec).
 *    The loader wires these to timers/fetch; everything here is pure.
 */

import type { Campaign, SteptSettings } from './types'

export interface SteptCommandHandlers {
  boot: (settings?: SteptSettings) => void
  open: () => void
  close: () => void
  toggle: () => void
  show: () => void
  hide: () => void
  shutdown: () => void
  startTour: (tourId: string) => void
  /** Register a client action the AI assistant may run (`actions.ts`). */
  action: (def?: unknown) => void
  removeAction: (name: string) => void
}

/** The public callable. `q` holds calls queued before the real fn was installed. */
export type SteptFn = ((command: string, ...args: unknown[]) => void) & {
  q?: Array<ArrayLike<unknown>>
}

/** Build the dispatcher that routes command strings to handlers. */
export function createDispatcher(handlers: SteptCommandHandlers): SteptFn {
  const dispatch = ((command: string, ...args: unknown[]): void => {
    switch (command) {
      case 'boot':
        handlers.boot(args[0] as SteptSettings | undefined)
        break
      case 'open':
        handlers.open()
        break
      case 'close':
        handlers.close()
        break
      case 'toggle':
        handlers.toggle()
        break
      case 'show':
        handlers.show()
        break
      case 'hide':
        handlers.hide()
        break
      case 'shutdown':
        handlers.shutdown()
        break
      case 'startTour':
        handlers.startTour(String(args[0] ?? ''))
        break
      case 'action':
        handlers.action(args[0])
        break
      case 'removeAction':
        handlers.removeAction(String(args[0] ?? ''))
        break
      default:
        if (typeof console !== 'undefined') {
          console.warn(`[stept] unknown command: ${String(command)}`)
        }
    }
  }) as SteptFn
  return dispatch
}

/**
 * Install the real dispatcher on `win.Stept`, replaying any queued calls.
 *
 * Supports two host patterns:
 *  - the official snippet just loads loader.js (no pre-calls) — nothing to drain;
 *  - an advanced snippet stubs `window.Stept` as a queue collector with a `.q`
 *    array, allowing `Stept('boot', …)` before the script loads.
 */
export function installStept(
  win: { Stept?: SteptFn },
  handlers: SteptCommandHandlers,
): SteptFn {
  const existing = win.Stept
  const dispatch = createDispatcher(handlers)
  const queued: Array<ArrayLike<unknown>> =
    existing && Array.isArray(existing.q) ? existing.q : []
  win.Stept = dispatch
  for (const call of queued) {
    const [command, ...args] = Array.from(call as ArrayLike<unknown>)
    dispatch(command as string, ...args)
  }
  return dispatch
}

// --- proactive campaigns: pure decision logic --------------------------------

/**
 * fnmatch-style glob match, mirroring the backend's URL matching for tours,
 * checklists and surveys (`*` = any run of characters, `?` = any single
 * character; whole-string match). Everything else is treated literally.
 *
 * CASE-INSENSITIVE on purpose: the backend matches with Python's `fnmatch`,
 * which normalizes case on macOS/Windows, so a case-sensitive client would
 * disagree with the server about which experiences are eligible. `[seq]`
 * classes stay backend-only (documented in docs/DAP2-CONTRACTS.md #9).
 */
export function globMatch(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&')
  const source = `^${escaped.replace(/\*/g, '.*').replace(/\?/g, '.')}$`
  try {
    return new RegExp(source, 'i').test(value)
  } catch {
    return false
  }
}

// --- preview links -----------------------------------------------------------

export interface PreviewRequest {
  token: string
  /** Tour id, from an explicit hash param or the token's `tour` claim. */
  tourId: string
}

/** Read a claim out of a JWT payload WITHOUT verifying it — the server is the
 * only authority; the widget just needs the tour id to build the request URL. */
export function decodeTokenClaim(token: string, claim: string): string | null {
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const decoded = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='))) as Record<
      string,
      unknown
    >
    const value = decoded[claim]
    return typeof value === 'string' ? value : null
  } catch {
    return null
  }
}

/**
 * Parse `#stept-preview=<token>` (optionally `&stept-preview-tour=<id>`) out of
 * a location hash. The dashboard appends it to any page URL to preview a tour
 * regardless of status/trigger/frequency.
 */
export function parsePreviewHash(hash: string): PreviewRequest | null {
  const token = /[#&?]stept-preview=([^&\s]+)/.exec(hash || '')?.[1]
  if (!token) return null
  const decodedToken = safeDecode(token)
  const explicit = /[#&?]stept-preview-tour=([^&\s]+)/.exec(hash)?.[1]
  const tourId = explicit ? safeDecode(explicit) : decodeTokenClaim(decodedToken, 'tour')
  return tourId ? { token: decodedToken, tourId } : null
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

// --- SPA navigation hook ------------------------------------------------------

/** Where the untouched history methods are parked, so a re-boot can never stack
 * wrappers (each stacked layer re-dispatched the event, N× per navigation). */
const HISTORY_ORIGINALS = Symbol.for('stept.history.originals')

interface HistoryOriginals {
  pushState: History['pushState']
  replaceState: History['replaceState']
}

type PatchHost = Record<symbol, HistoryOriginals | undefined>

/**
 * Patch `history.pushState`/`replaceState` to emit `stept:locationchange`.
 * Idempotent: the originals are parked on a window symbol, so calling this
 * again (a re-`boot`) is a no-op. Returns true when it actually patched.
 */
export function patchHistory(win: Window): boolean {
  const host = win as unknown as PatchHost
  if (host[HISTORY_ORIGINALS]) return false
  const history = win.history
  if (!history) return false
  const originals: HistoryOriginals = {
    pushState: history.pushState,
    replaceState: history.replaceState,
  }
  host[HISTORY_ORIGINALS] = originals
  for (const method of ['pushState', 'replaceState'] as const) {
    const original = originals[method]
    history[method] = function (this: History, ...args: Parameters<History['pushState']>) {
      const result = original.apply(this, args)
      win.dispatchEvent(new Event('stept:locationchange'))
      return result
    }
  }
  return true
}

/** Undo {@link patchHistory}. Returns true when a patch was removed. */
export function restoreHistory(win: Window): boolean {
  const host = win as unknown as PatchHost
  const originals = host[HISTORY_ORIGINALS]
  if (!originals) return false
  win.history.pushState = originals.pushState
  win.history.replaceState = originals.replaceState
  host[HISTORY_ORIGINALS] = undefined
  return true
}

/** localStorage key holding the triggered/skipped campaign ids for one widget. */
export function campaignSeenKey(widgetKey: string): string {
  return `stept:${widgetKey}:campaigns`
}

/** Delay before an eligible campaign fires (time_on_page_seconds, default 0). */
export function campaignDelayMs(campaign: Campaign): number {
  const t = campaign.trigger_rules?.time_on_page_seconds
  return typeof t === 'number' && Number.isFinite(t) && t > 0 ? Math.round(t * 1000) : 0
}

/**
 * Campaigns that may fire on this page view: not already seen, and either no
 * url_pattern or a glob match against the current URL. Order is preserved.
 */
export function selectEligibleCampaigns(
  campaigns: readonly Campaign[],
  url: string,
  seen: Iterable<string> = [],
): Campaign[] {
  const seenSet = seen instanceof Set ? seen : new Set(seen)
  return campaigns.filter((c) => {
    if (seenSet.has(c.id)) return false
    const pattern = c.trigger_rules?.url_pattern
    if (typeof pattern === 'string' && pattern.trim()) return globMatch(pattern, url)
    return true
  })
}

/**
 * The single campaign that fires on this page view: the one due first
 * (smallest delay), ties broken by list order. Others stay unfired (and
 * unseen) so they can trigger on later page views.
 */
export function firstDueCampaign(eligible: readonly Campaign[]): Campaign | null {
  let best: Campaign | null = null
  let bestDelay = Infinity
  for (const campaign of eligible) {
    const delay = campaignDelayMs(campaign)
    if (delay < bestDelay) {
      best = campaign
      bestDelay = delay
    }
  }
  return best
}

/** Drop seen ids whose campaign no longer exists (keeps localStorage bounded). */
export function pruneSeenCampaigns(
  seen: Iterable<string>,
  campaigns: ReadonlyArray<Pick<Campaign, 'id'>>,
): Set<string> {
  const live = new Set(campaigns.map((c) => c.id))
  const pruned = new Set<string>()
  for (const id of seen) {
    if (live.has(id)) pruned.add(id)
  }
  return pruned
}

/** Read a JSON string-array set from storage; tolerant of junk/private mode. */
export function readSeenSet(storage: Pick<Storage, 'getItem'> | null, key: string): Set<string> {
  try {
    const raw = storage?.getItem(key)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return new Set(
      Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [],
    )
  } catch {
    return new Set()
  }
}

/** Persist a set of ids as a JSON array; silently a no-op in private mode. */
export function writeSeenSet(
  storage: Pick<Storage, 'setItem'> | null,
  key: string,
  ids: Iterable<string>,
): void {
  try {
    storage?.setItem(key, JSON.stringify([...ids]))
  } catch {
    /* private mode — session only */
  }
}
