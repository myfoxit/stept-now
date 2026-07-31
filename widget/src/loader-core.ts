/**
 * The `window.Stept(command, ...args)` command API and its queue-replay shim.
 *
 * Kept free of any DOM/iframe wiring so it can be unit-tested in isolation. The
 * loader supplies concrete handlers; this module only routes commands and drains
 * any calls the host queued before loader.js finished loading (Intercom-style).
 */

import type { SteptSettings } from './types'

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
