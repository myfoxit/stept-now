/**
 * Client actions: the host app's own verbs, registered via `Stept('action', …)`.
 *
 * The registry lives in the LOADER (host page) because that is where the
 * handlers are: developer code, running with the signed-in user's session. The
 * iframe app only ever sees the function-free wire defs — it advertises them to
 * the backend with page context / outgoing messages, renders the confirm card,
 * and forwards an approved op back across the bridge for execution here.
 *
 * Everything in this module is pure host-page logic with no DOM/iframe wiring,
 * mirroring loader-core.ts, so it unit-tests in isolation.
 */

/** What the developer passes to `Stept('action', …)`. */
export interface ClientActionDef {
  /** ^[a-z][a-z0-9_]{0,47}$ — also the name the model calls (`app_<name>`). */
  name: string
  /** What this does, in words the model plans with. Required. */
  description: string
  /** JSON-Schema object (same subset custom actions use). Omit for "no args". */
  params?: Record<string, unknown>
  /** Ask the visitor in the thread before running. Default true. */
  confirm?: boolean
  /** Pause the run for TEAM approval (the existing durable gate). Default false. */
  approval?: boolean
  /** Only offer this when the visitor booted with verified identity. */
  requiresIdentity?: boolean
  /** The handler. Runs in the host page; may return string | JSON-able value. */
  run: (params: Record<string, unknown>) => unknown
}

/** The function-free shape that crosses the bridge and the wire. */
export interface ClientActionWireDef {
  name: string
  description: string
  params?: Record<string, unknown>
  confirm: boolean
  approval: boolean
  requires_identity: boolean
}

export const ACTION_NAME_RE = /^[a-z][a-z0-9_]{0,47}$/
export const HANDLER_TIMEOUT_MS = 30_000
/** Client-side cap on a serialized result (the server backstops at 24k). */
export const MAX_RESULT_CHARS = 8_000

/** `{ok, result}` | `{ok:false, error}` — travels into the model's tool result. */
export type ActionResult = Record<string, unknown>

export class ActionRegistry {
  private defs = new Map<string, ClientActionDef>()

  /** Validate + store a def (replacing any previous registration of the name). */
  register(raw: unknown): boolean {
    const def = raw as Partial<ClientActionDef> | null
    if (!def || typeof def !== 'object') {
      warn('ignored an action registration that is not an object')
      return false
    }
    if (typeof def.name !== 'string' || !ACTION_NAME_RE.test(def.name)) {
      warn(`ignored action with invalid name ${JSON.stringify(def.name)} (want ^[a-z][a-z0-9_]{0,47}$)`)
      return false
    }
    if (typeof def.description !== 'string' || !def.description.trim()) {
      warn(`action '${def.name}' needs a description — the model plans with it`)
      return false
    }
    if (typeof def.run !== 'function') {
      warn(`action '${def.name}' needs a run() handler`)
      return false
    }
    if (def.params !== undefined && (typeof def.params !== 'object' || def.params === null)) {
      warn(`action '${def.name}' has a non-object params schema — ignored`)
      return false
    }
    this.defs.delete(def.name)
    this.defs.set(def.name, def as ClientActionDef)
    return true
  }

  remove(name: string): boolean {
    return this.defs.delete(name)
  }

  clear(): void {
    this.defs.clear()
  }

  size(): number {
    return this.defs.size
  }

  /** The advertised (function-free) defs, in registration order. */
  wireDefs(): ClientActionWireDef[] {
    const out: ClientActionWireDef[] = []
    for (const def of this.defs.values()) {
      out.push({
        name: def.name,
        description: def.description,
        ...(def.params ? { params: def.params } : {}),
        confirm: def.confirm !== false,
        approval: def.approval === true,
        requires_identity: def.requiresIdentity === true,
      })
    }
    return out
  }

  /**
   * Run one action and shape the outcome as a tool result.
   *
   * Never throws into the host page: a missing handler (the visitor navigated
   * away from the page that registered it), a rejection, or a hang all become
   * `{ok:false, error}` the model can read and explain.
   */
  async execute(name: string, params: Record<string, unknown>): Promise<ActionResult> {
    const def = this.defs.get(name)
    if (!def) {
      return { ok: false, error: `action '${name}' is not available on this page` }
    }
    try {
      const value = await withTimeout(Promise.resolve(def.run(params || {})), HANDLER_TIMEOUT_MS)
      return { ok: true, result: serializeResult(value) }
    } catch (err) {
      return { ok: false, error: describeError(err) }
    }
  }
}

/** Handler return → the string the model reads (size-capped, never throws). */
export function serializeResult(value: unknown): string {
  if (value === undefined || value === null) return 'done'
  if (typeof value === 'string') return value.slice(0, MAX_RESULT_CHARS)
  try {
    return JSON.stringify(value).slice(0, MAX_RESULT_CHARS)
  } catch {
    return String(value).slice(0, MAX_RESULT_CHARS)
  }
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`action timed out after ${ms / 1000}s`)), ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (err: unknown) => {
        clearTimeout(timer)
        reject(err instanceof Error ? err : new Error(String(err)))
      },
    )
  })
}

function describeError(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err)
  return (text || 'the action failed').slice(0, 500)
}

function warn(message: string): void {
  if (typeof console !== 'undefined') console.warn(`[stept] ${message}`)
}
