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
 * fnmatch-style glob match, mirroring the backend's tour URL matching
 * (`*` = any run of characters, `?` = any single character; whole-string
 * match, case-sensitive). Everything else is treated literally.
 */
export function globMatch(pattern: string, value: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&')
  const source = `^${escaped.replace(/\*/g, '.*').replace(/\?/g, '.')}$`
  try {
    return new RegExp(source).test(value)
  } catch {
    return false
  }
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
