/**
 * @stept/js — typed, SSR-safe wrapper around the Stept widget loader.
 *
 * The loader itself stays a two-line script tag under the hood: `loadStept()`
 * injects it and every command proxies to `window.Stept`, queueing
 * Intercom-style (`window.Stept.q`) until the script arrives, so every call —
 * including `registerAction` — is safe to make before, during, or after load.
 *
 * All functions are no-ops outside a browser, so importing from server-rendered
 * code never throws.
 */

/** Identity handshake (HMAC of external_id with your workspace secret). */
export interface SteptIdentity {
  external_id: string
  email?: string
  name?: string
  hash: string
}

export interface SteptSettings {
  /** The public widget key (`wk_…`) from Settings → Channels → Widget. */
  workspaceKey: string
  /** Origin of your Stept instance, e.g. https://stept.example.com */
  apiBase: string
  identity?: SteptIdentity
  /** Extra origins the AI assistant may navigate to while acting for the user. */
  aiAllowedOrigins?: string[]
}

/** A function the AI assistant may call — your app's own verb. */
export interface ClientActionDef {
  /** ^[a-z][a-z0-9_]{0,47}$ — the model calls it as `app_<name>`. */
  name: string
  /** What this does, in words the model plans with. Required. */
  description: string
  /** JSON-Schema object describing the arguments. Omit for "no arguments". */
  params?: Record<string, unknown>
  /** Show a Run / Not now card in the thread before executing. Default true. */
  confirm?: boolean
  /** Pause the run for approval by YOUR team before executing. Default false. */
  approval?: boolean
  /** Only offer this action for identity-verified users. Default false. */
  requiresIdentity?: boolean
  /** Runs in the page with the signed-in user's session. */
  run: (params: Record<string, unknown>) => unknown
}

type SteptFn = ((command: string, ...args: unknown[]) => void) & { q?: unknown[][] }

interface SteptWindow extends Window {
  Stept?: SteptFn
  SteptSettings?: SteptSettings
}

const SCRIPT_ID = 'stept-loader-script'

function win(): SteptWindow | null {
  return typeof window === 'undefined' ? null : (window as unknown as SteptWindow)
}

/** The queue-or-call shim: usable before `loadStept` and before the script lands. */
export function stept(command: string, ...args: unknown[]): void {
  const w = win()
  if (!w) return
  if (!w.Stept) {
    const stub: SteptFn = (...call: unknown[]) => {
      ;(stub.q = stub.q || []).push(call)
    }
    stub.q = []
    w.Stept = stub
  }
  w.Stept(command, ...args)
}

/**
 * Inject the widget loader (idempotent) and boot it with `settings`.
 *
 * Calling again with new settings re-boots the widget (e.g. after login, to
 * pass identity).
 */
export function loadStept(settings: SteptSettings): void {
  const w = win()
  if (!w) return
  w.SteptSettings = settings
  if (w.document.getElementById(SCRIPT_ID)) {
    // Already injected — a second call is a re-boot with the new settings.
    stept('boot', settings)
    return
  }
  const script = w.document.createElement('script')
  script.id = SCRIPT_ID
  script.async = true
  script.src = `${settings.apiBase.replace(/\/+$/, '')}/widget-assets/loader.js`
  w.document.head.appendChild(script)
}

/** Teach the assistant one of your app's actions. Same-name calls replace. */
export function registerAction(def: ClientActionDef): void {
  stept('action', def)
}

/** Remove a registered action (e.g. the screen offering it went away). */
export function removeAction(name: string): void {
  stept('removeAction', name)
}

export function openStept(): void {
  stept('open')
}

export function closeStept(): void {
  stept('close')
}

export function startTour(tourId: string): void {
  stept('startTour', tourId)
}

/** Tear the widget down (keeps registered actions for the next boot). */
export function shutdownStept(): void {
  stept('shutdown')
}
