import type { PanelState, PickedSelector, RawEvent, TourStep } from './types';
import type { GuideStepPayload } from './guide/guide-core';

/** Message contracts: content scripts ↔ background ↔ side panel.
 *
 * The background is the session anchor (state journaled to
 * `chrome.storage.session` so an MV3 service-worker teardown never loses a
 * recording); the panel is a pure view that re-requests state on mount and
 * then follows broadcasts. Ported from the old repo's `messages.ts`, trimmed
 * to the flows this extension actually has.
 */

// ---------------------------------------------------------------------------
// content → background
// ---------------------------------------------------------------------------

export type ContentToBg =
  /** `captureToken` pairs the event with the pre-capture its pointerdown fired
   * — a held screenshot is only ever attached to the event echoing the token,
   * never to whatever event happens to arrive first (see CaptureHold). */
  | { type: 'event'; event: RawEvent; captureToken?: string }
  | { type: 'content-ready' }
  /** pointerdown fired: screenshot NOW, before click effects repaint. */
  | { type: 'pre-capture'; token: string }
  /** Guide overlay → engine, anchored to the step index it happened on so a
   * stale message from a page we already advanced past can't double-advance. */
  | {
      type: 'guide-event';
      event: 'advance' | 'back' | 'stop' | 'notfound' | 'found';
      index: number;
    }
  /** Selector picker captured an element (or the user pressed Escape). */
  | { type: 'picked'; picked: PickedSelector | null };

export type BgToContent = { type: 'set-recording'; recording: boolean };

/** Background → guide overlay (tabs.sendMessage to the guided tab). */
export type BgToGuideContent =
  | { type: 'guide-show'; step: GuideStepPayload }
  | { type: 'guide-hide' };

/** Background → driver island. Every call is request/response. */
export type BgToDriver =
  | { type: 'driver-resolve'; step: TourStep }
  | { type: 'driver-prepare'; step: TourStep }
  | { type: 'driver-act'; step: TourStep; kind: 'click' | 'fill'; value?: string }
  | { type: 'driver-highlight'; step: TourStep; ms: number }
  | { type: 'driver-banner'; text: string; ms: number }
  | { type: 'driver-wait-element'; step: TourStep; timeoutMs: number }
  | { type: 'driver-settle'; quietMs: number; maxMs: number };

/** What the driver island reports back for a resolve/prepare. */
export interface DriverResolveResult {
  found: boolean;
  /** viewport CSS-pixel click point (element centre, occlusion-adjusted) */
  x?: number;
  y?: number;
  healed?: boolean;
  via?: string;
  confidence?: number;
  detail?: string;
  contentEditable?: boolean;
  /** something else is painted over the click point */
  occluded?: boolean;
}

// ---------------------------------------------------------------------------
// side panel → background
// ---------------------------------------------------------------------------

export type PanelToBg =
  | { type: 'get-state' }
  | { type: 'sign-in'; apiBase: string; email: string; password: string }
  | { type: 'choose-workspace'; workspaceId: string }
  | { type: 'adopt-token'; apiBase: string; token: string }
  | { type: 'sign-out' }
  | { type: 'refresh-tours' }
  | { type: 'start-recording'; tabId?: number }
  | { type: 'stop-recording' }
  | { type: 'pause-recording'; paused: boolean }
  /** delete raw events by index (a compiled step's `sources`) */
  | { type: 'delete-events'; indexes: number[] }
  | { type: 'retitle-step'; stepId: string; title: string }
  | { type: 'set-step-body'; stepId: string; body: string }
  | { type: 'reorder-steps'; order: string[] }
  | { type: 'discard-recording' }
  | { type: 'save-tour'; name: string; urlPattern?: string }
  /** pull a tour into the panel for editing (PUT-back with base_version) */
  | { type: 'pull-tour'; tourId: string }
  | { type: 'close-editing' }
  | { type: 'edit-step'; stepId: string; patch: Partial<TourStep> }
  | { type: 'edit-delete-step'; stepId: string }
  | { type: 'edit-move-step'; stepId: string; dir: -1 | 1 }
  | { type: 'push-tour' }
  | { type: 'guide-start'; tourId: string }
  | { type: 'guide-stop' }
  | { type: 'guide-nav'; dir: 1 | -1 }
  | { type: 'drive-start'; tourId: string }
  | { type: 'drive-stop' }
  | { type: 'drive-pause'; paused: boolean }
  | { type: 'drive-speed'; speed: number }
  /** resolution of an error surfaced mid-drive */
  | { type: 'drive-decide'; decision: 'skip' | 'abort' | 'retry' }
  | { type: 'picker-start' }
  | { type: 'clear-picked' };

export type BgToPanel =
  | { type: 'state'; state: PanelState }
  | { type: 'error'; message: string };

/** Reply shapes for the request/response panel messages. */
export interface SignInResult {
  ok: boolean;
  error?: string;
  /** more than one workspace → the panel shows the chooser */
  workspaces?: Array<{ id: string; name: string; role: string; canManageTours: boolean }>;
}

export interface SimpleResult {
  ok: boolean;
  error?: string;
}
