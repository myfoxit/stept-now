/**
 * Step → element resolution for the tour player.
 *
 * A thin adapter over the shared `@stept/dom-capture` engine (the SAME code the
 * Chrome extension recorder/player uses — never fork it):
 *
 *  - a step recorded by the v2 extension carries the rich `target` descriptor →
 *    run the full cascade (ranked selectors → fingerprint hashes → accessible
 *    name → structural scoring);
 *  - an older / hand-authored step only carries the SIMPLE projection
 *    (`selector`, `fallback_selectors`, `text_hint`) → synthesize a minimal
 *    Target from it and run the same cascade, with the text hint as the final
 *    scan.
 *
 * Anything that resolves via something other than the primary selector is a
 * self-heal (`healed: true` → `meta.healed` on the step_viewed event); nothing
 * resolving at all is a `step_error` with `meta.reason`.
 */

import {
  isVisibleLenient,
  preferLaidOut,
  resolveSelector,
  resolveTarget,
  type RankedSelector,
  type SelectorKind,
  type Target,
} from '@stept/dom-capture'

import type { TourStep } from './types'

/** Which tier produced the element; `text_hint` is this adapter's own last resort. */
export type StepResolveVia = 'primary' | 'fallback' | 'fingerprint' | 'scored' | 'text_hint' | 'none'

/** Why a step could not be shown — travels as `meta.reason` on `step_error`. */
export type StepErrorReason = 'not_found' | 'in_iframe' | 'timeout'

export interface StepResolution {
  el: HTMLElement | null
  /** true when the primary selector did NOT produce the hit (self-heal). */
  healed: boolean
  via: StepResolveVia
  /** Only set when `el === null`. */
  reason?: StepErrorReason
  /** Cascade trace, handy when debugging a customer page from the console. */
  detail?: string
}

const KIND_PREFIX = /^(aria|text|xpath|pierce)\//
const NOT_FOUND: StepResolution = { el: null, healed: false, via: 'none', reason: 'not_found' }

/** Step types that are anchored to a page element (the rest float). */
const ANCHORED_TYPES = new Set(['tooltip', 'hotspot', 'action'])

/** Does this step need an element resolved before it can be shown? */
export function stepNeedsTarget(step: TourStep): boolean {
  const type = step.type ?? 'tooltip'
  if (type === 'wait') return (step.wait?.for ?? 'element') === 'element'
  return ANCHORED_TYPES.has(type) && Boolean(selectorOf(step))
}

/** The selector a step resolves against (wait steps may carry their own). */
export function selectorOf(step: TourStep): string {
  if ((step.type ?? 'tooltip') === 'wait') return (step.wait?.selector || step.selector || '').trim()
  return (step.selector || '').trim()
}

/**
 * Split a DevTools-style selector string into its kind + value.
 * `aria/Save[role="button"]` → aria, `#save` → css. Idempotent.
 */
export function parseSelectorString(raw: string): { kind: SelectorKind; value: string } {
  const value = (raw || '').trim()
  const match = KIND_PREFIX.exec(value)
  return match ? { kind: match[1] as SelectorKind, value } : { kind: 'css', value }
}

/**
 * The Target the cascade runs against: the recorded descriptor when present,
 * else a minimal one synthesized from the simple projection.
 *
 * The synthesized Target intentionally carries selectors ONLY — no text/aria
 * evidence. `resolveTarget` VERIFIES selector hits against whatever identity
 * the target claims, so feeding it a stale `text_hint` would make a perfectly
 * good primary selector fail. The hint is used as an explicit last-resort scan
 * instead (see {@link resolveStepTarget}).
 */
export function stepTarget(step: TourStep): Target {
  if (step.target && Array.isArray(step.target.selectors) && step.target.selectors.length) {
    return step.target
  }
  const selectors: RankedSelector[] = []
  const push = (raw: string, score: number): void => {
    const parsed = parseSelectorString(raw)
    if (!parsed.value) return
    if (selectors.some((s) => s.value === parsed.value)) return
    selectors.push({ ...parsed, score })
  }
  push(selectorOf(step), 0.9)
  const fallbacks = step.fallback_selectors ?? []
  fallbacks.forEach((sel, i) => push(sel, 0.8 - i * 0.05))
  return { selectors }
}

/** The recorded text hint, from the rich target or the simple projection. */
export function textHintOf(step: TourStep): string {
  return (step.text_hint || step.target?.text?.content || step.target?.aria?.name || '').trim()
}

/**
 * Resolve a step's element in `doc` (the TOP document only).
 *
 * v1 scope: cross-frame steps are reported as `in_iframe` rather than silently
 * failing — the widget cannot script frames it does not own, the extension
 * player handles them.
 */
export function resolveStepTarget(step: TourStep, doc: Document = document): StepResolution {
  const target = stepTarget(step)
  if (target.frame && target.frame.length > 0) {
    return { el: null, healed: false, via: 'none', reason: 'in_iframe' }
  }
  if (!target.selectors.length && !textHintOf(step)) return NOT_FOUND

  if (target.selectors.length) {
    const result = resolveTarget(doc, target)
    if (result.element) {
      return {
        el: result.element as HTMLElement,
        healed: result.healed,
        via: result.via,
        detail: result.detail,
      }
    }
  }

  // Last resort: scan for the recorded visible text. Always a self-heal.
  const hint = textHintOf(step)
  if (hint) {
    const hits = preferLaidOut(resolveSelector('text', hint, doc).filter(isVisibleLenient))
    if (hits.length) {
      return { el: hits[0] as HTMLElement, healed: true, via: 'text_hint', detail: 'text hint scan' }
    }
  }
  return NOT_FOUND
}

export interface WaitOptions {
  doc?: Document
  win?: Pick<Window, 'setTimeout' | 'clearTimeout' | 'setInterval' | 'clearInterval'>
  /** Polling cadence backing up the MutationObserver. */
  pollMs?: number
}

/**
 * Resolve a step's element, waiting for it to appear (SPA renders after the
 * URL change). Observes DOM mutations AND polls, resolving as soon as either
 * sees the element; on timeout it resolves with the failure reason rather than
 * rejecting, so callers can emit `step_error` without a try/catch.
 */
export function waitForTarget(
  step: TourStep,
  timeoutMs = 5000,
  opts: WaitOptions = {},
): Promise<StepResolution> {
  const doc = opts.doc ?? document
  const win = opts.win ?? (doc.defaultView as Window) ?? (globalThis as unknown as Window)
  const pollMs = opts.pollMs ?? 300

  const immediate = resolveStepTarget(step, doc)
  // An in-iframe step will never resolve here — fail fast instead of waiting.
  if (immediate.el || immediate.reason === 'in_iframe' || timeoutMs <= 0) {
    return Promise.resolve(immediate.el ? immediate : { ...immediate, reason: immediate.reason ?? 'not_found' })
  }

  return new Promise<StepResolution>((resolve) => {
    let done = false
    const finish = (result: StepResolution): void => {
      if (done) return
      done = true
      observer?.disconnect()
      win.clearInterval(poll)
      win.clearTimeout(timer)
      resolve(result)
    }
    const attempt = (): void => {
      const result = resolveStepTarget(step, doc)
      if (result.el) finish(result)
    }

    const Observer = (doc.defaultView as (Window & typeof globalThis) | null)?.MutationObserver
    const observer = Observer ? new Observer(attempt) : null
    if (observer && doc.documentElement) {
      observer.observe(doc.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
      })
    }
    const poll = win.setInterval(attempt, pollMs)
    const timer = win.setTimeout(() => finish({ ...NOT_FOUND, reason: 'timeout' }), timeoutMs)
  })
}
