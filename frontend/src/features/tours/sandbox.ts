/**
 * Sandbox playback: turning a stored DOM replica back into a viewable screen.
 *
 * The capture side lives in `packages/dom-capture/src/snapshot.ts`, which also
 * owns `renderSnapshot` — the canonical version of `buildSandboxDoc` below. The
 * dashboard does not depend on that workspace package, so the ~20 lines of
 * document assembly are restated here rather than pulled in; the envelope shape
 * is a stable contract (see `SNAPSHOT_VERSION`) and both sides ship tests
 * asserting the same guarantees. If the frontend ever takes that dependency,
 * delete this and import `renderSnapshot`.
 *
 * SECURITY: `buildSandboxDoc` returns untrusted markup captured from a
 * customer's page. It may only be mounted in an iframe carrying
 * `sandbox={SANDBOX_ATTR}` — present, and omitting both `allow-scripts` and
 * `allow-same-origin`. Anything looser executes the captured app's markup on a
 * Stept origin.
 */

import { targetBBox, type StepDraft, type TargetBox } from './lib'

/** The `sandbox` attribute a replica iframe must carry. Empty = full lockdown. */
export const SANDBOX_ATTR = ''

/** Envelope written by `@stept/dom-capture`'s `captureSnapshot`. */
export interface PageSnapshot {
  version?: number
  html: string
  css?: string[]
  url?: string
  title?: string
  viewport?: { w: number; h: number }
  scroll?: { x: number; y: number }
  blockedStyles?: string[]
  omitted?: { frames: number; canvases: number; masked: number }
}

/** A stored envelope, or a clear reason why this screen cannot be replayed. */
export type SandboxScreen =
  | { kind: 'replica'; snapshot: PageSnapshot }
  | { kind: 'screenshot'; src: string }
  | { kind: 'empty' }

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** Assemble one self-contained document for `iframe.srcdoc`. */
export function buildSandboxDoc(snapshot: PageSnapshot): string {
  const css = (snapshot.css ?? []).join('\n')
  return [
    '<!doctype html>',
    '<meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="script-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'">`,
    `<title>${escapeHtml(snapshot.title ?? '')}</title>`,
    snapshot.url ? `<base href="${escapeHtml(snapshot.url)}">` : '',
    css ? `<style>${css}</style>` : '',
    // The replica is a still: pointer interaction with native controls is fine,
    // but nothing should be able to scroll it out from under the guide chrome.
    '<style>a[data-stept-href]{cursor:pointer}' +
      '[data-stept-omitted]{background:repeating-linear-gradient(45deg,#0000,#0000 6px,#8881 6px,#8881 12px)}' +
      '</style>',
    snapshot.html,
  ]
    .filter(Boolean)
    .join('\n')
}

/** True when the replica is missing enough that the author should be warned. */
export function snapshotWarnings(snapshot: PageSnapshot): string[] {
  const warnings: string[] = []
  const blocked = snapshot.blockedStyles?.length ?? 0
  if (blocked > 0) {
    warnings.push(
      `${blocked} stylesheet${blocked === 1 ? '' : 's'} could not be read at capture time, so this screen may look unstyled.`
    )
  }
  if (snapshot.omitted?.frames) {
    warnings.push(`${snapshot.omitted.frames} embedded frame(s) were left out.`)
  }
  if (snapshot.omitted?.canvases) {
    warnings.push(`${snapshot.omitted.canvases} canvas element(s) could not be copied.`)
  }
  return warnings
}

export interface OverlayBox {
  /** Percentages of the replica's own recorded viewport. */
  left: number
  top: number
  width: number
  height: number
}

/** The recorded element as percentages, ready for absolute positioning. */
export function overlayBox(box: TargetBox): OverlayBox {
  return {
    left: (box.x / box.vw) * 100,
    top: (box.y / box.vh) * 100,
    width: (box.w / box.vw) * 100,
    height: (box.h / box.vh) * 100,
  }
}

export type SandboxSide = 'top' | 'bottom'

/**
 * Which side of the highlighted element the guide card sits on.
 *
 * Percentage-space rather than pixels: the replica scales with the viewer's
 * window, so a decision made in captured pixels would be wrong at any other
 * size. `card` is the card's height as a percentage of the frame.
 */
export function sandboxCardSide(box: OverlayBox, card = 22): SandboxSide {
  const below = 100 - (box.top + box.height)
  return below >= card || below >= box.top ? 'bottom' : 'top'
}

/**
 * What to show for a step: its replica if one was captured, otherwise the
 * screenshot, otherwise nothing.
 *
 * The screenshot fallback is why sandbox playback works on tours recorded long
 * before replicas existed — it just is not interactive.
 */
export function screenFor(
  draft: StepDraft,
  snapshot: PageSnapshot | undefined,
  screenshotSrc: string | null
): SandboxScreen {
  if (draft.sandboxKey && snapshot) return { kind: 'replica', snapshot }
  if (screenshotSrc) return { kind: 'screenshot', src: screenshotSrc }
  return { kind: 'empty' }
}

/** The recorded geometry for a step, or null when it was never anchored. */
export function stepBox(draft: StepDraft): OverlayBox | null {
  const box = targetBBox(draft.target)
  return box ? overlayBox(box) : null
}
