import type { Target } from '@stept/dom-capture';

/** The wire contracts shared by every layer of the extension.
 *
 * `TourStep` mirrors backend `app/schemas/tours.py::TourStepIn/Out` EXACTLY
 * (docs/DAP2-CONTRACTS.md step schema v2) — including the `wait.for` key, which
 * is a Python keyword server-side but stays `for` on the wire. The rich
 * `target` is opaque JSON to the backend and is passed straight through.
 */

export type TourStepType = 'tooltip' | 'modal' | 'banner' | 'hotspot' | 'action' | 'wait';
export type StepPlacement = 'auto' | 'top' | 'bottom' | 'left' | 'right' | 'center';
export type AdvanceOn = 'button' | 'element_click' | 'input' | 'delay';

export interface StepAdvance {
  on: AdvanceOn;
  delay_ms?: number;
}

export interface StepAction {
  kind: 'click' | 'fill' | 'navigate';
  value?: string;
  url?: string;
}

export interface StepWait {
  /** wire key is `for` — the backend aliases it to `for_` */
  for: 'element' | 'url';
  selector?: string;
  url_pattern?: string;
  timeout_ms: number;
}

export interface StepMedia {
  type: 'image' | 'video';
  url: string;
}

export interface TourStep {
  id: string;
  type: TourStepType;
  selector: string;
  fallback_selectors: string[];
  text_hint: string;
  /** full @stept/dom-capture Target — the healing fuel, ≤8KB server-side */
  target?: Target | null;
  title: string;
  /** markdown */
  body: string;
  media?: StepMedia | null;
  screenshot_key?: string | null;
  /** public key of the DOM replica backing sandbox playback for this step */
  sandbox_key?: string | null;
  placement: StepPlacement;
  advance: StepAdvance;
  action?: StepAction | null;
  wait?: StepWait | null;
}

export interface TourSummary {
  id: string;
  name: string;
  kind: string;
  status: string;
  steps_count: number;
  version: number;
  updated_at: string;
}

export interface TourDetail {
  id: string;
  name: string;
  description: string;
  kind: string;
  status: string;
  steps: TourStep[];
  version: number;
  settings: {
    mode: 'guided' | 'driven';
    backdrop: boolean;
    show_progress: boolean;
    dismissable: boolean;
  };
  trigger: { type: string; url_pattern: string | null };
}

// ---------------------------------------------------------------------------
// recording: raw events (the recorder's output, the compiler's input)
// ---------------------------------------------------------------------------

interface RawBase {
  /** DOM-event time; `orderEvents` sorts by it (sendMessage has no ordering) */
  t: number;
  tabId: number;
  frameId: number;
  pageTitle?: string;
  /** storage key of the eagerly-uploaded pre-action screenshot */
  screenshotKey?: string;
  /** storage key of the pre-action DOM replica (sandbox capture only) */
  sandboxKey?: string;
  url?: string;
}

export type RawEvent =
  | (RawBase & {
      kind: 'pointer';
      action: 'click' | 'dblclick' | 'context';
      point: { x: number; y: number };
      modifiers: string[];
      button: 'left' | 'middle' | 'right';
      context: Target;
    })
  | (RawBase & { kind: 'input'; context: Target; value: string; secret: boolean })
  | (RawBase & { kind: 'key'; keys: string; context?: Target })
  | (RawBase & { kind: 'select'; context: Target; value: string; label?: string })
  | (RawBase & { kind: 'check'; context: Target; checked: boolean })
  | (RawBase & { kind: 'upload'; context: Target; fileName: string })
  | (RawBase & { kind: 'hover'; context: Target; revealedText: string[] })
  | (RawBase & { kind: 'scroll'; x: number; y: number })
  | (RawBase & {
      kind: 'nav';
      url: string;
      transitionType: 'typed' | 'link' | 'auto';
      redirect: boolean;
    })
  | (RawBase & { kind: 'download'; filename: string; mime?: string })
  | (RawBase & { kind: 'tab'; action: 'created' | 'closed' });

export type RawEventKind = RawEvent['kind'];

// ---------------------------------------------------------------------------
// auth / session
// ---------------------------------------------------------------------------

export const DEFAULT_API_BASE = 'http://localhost:8600';

/** Everything persisted after sign-in. The access token and the password are
 * DISCARDED the moment the extension token is minted — only this survives. */
export interface StoredSession {
  apiBase: string;
  extensionToken: string;
  workspaceId: string;
  workspaceName: string;
  userName: string;
  /** Dashboard origin, learned from /dap/auth/check. Falls back to apiBase. */
  appBaseUrl: string;
}

export interface AuthState {
  signedIn: boolean;
  apiBase: string;
  workspaceId: string | null;
  workspaceName: string | null;
  userName: string | null;
  /** why we dropped to the signed-out screen (expired token, revoked member) */
  error: string | null;
  /** an auth/check round-trip is in flight */
  checking: boolean;
}

export interface WorkspaceChoice {
  id: string;
  name: string;
  role: string;
  canManageTours: boolean;
}

// ---------------------------------------------------------------------------
// guide / drive
// ---------------------------------------------------------------------------

export interface PanelStepRef {
  id: string;
  title: string;
  type: TourStepType;
}

export interface GuideState {
  tourId: string;
  name: string;
  tabId: number;
  index: number;
  total: number;
  steps: PanelStepRef[];
  status: 'active' | 'completed' | 'error';
  /** the current step's element cannot be found on the page right now */
  stuck: boolean;
  error: string | null;
}

export type DriveStepStatus = 'pending' | 'active' | 'done' | 'skipped' | 'error';

export interface DriveState {
  tourId: string;
  name: string;
  tabId: number;
  index: number;
  total: number;
  steps: PanelStepRef[];
  stepStatus: DriveStepStatus[];
  status: 'running' | 'paused' | 'completed' | 'error';
  /** 0.5 / 1 / 2 — scales every wait and the tooltip dwell time */
  speed: number;
  /** trusted CDP input, or the synthetic-events fallback */
  transport: 'cdp' | 'synthetic';
  error: string | null;
  /** the failure is recoverable: the panel offers Skip / Abort */
  awaitingDecision: boolean;
}

// ---------------------------------------------------------------------------
// remote drive (MCP → backend gateway → this extension)
// ---------------------------------------------------------------------------

/** One op the server may ask the driven tab to perform (W9 contract). */
export interface DriveOp {
  op:
    | 'open'
    | 'snapshot'
    | 'act'
    | 'navigate'
    | 'scroll'
    | 'key'
    | 'wait'
    | 'close'
    | 'page-text'
    | 'find'
    | 'console'
    | 'network'
    | 'extract'
    | 'back'
    | 'forward'
    | 'resize';
  args?: {
    url?: string;
    index?: number;
    kind?:
      | 'click'
      | 'double-click'
      | 'right-click'
      | 'hover'
      | 'type'
      | 'select'
      | 'check'
      | 'uncheck'
      | 'drag';
    text?: string;
    submit?: boolean;
    /** coordinate acts: screenshot pixel space (0,0 = top-left) */
    x?: number;
    y?: number;
    toX?: number;
    toY?: number;
    query?: string;
    pattern?: string;
    limit?: number;
    maxChars?: number;
    dir?: 'up' | 'down';
    amount?: number;
    key?: string;
    ms?: number;
    offset?: number;
    extractKind?: 'text' | 'attr' | 'url';
    attr?: string;
    width?: number;
    height?: number;
  };
}

/** What every executed op returns to the server (→ the MCP client). */
export interface DriveSnapshot {
  url: string;
  /** compact indexed listing — `[n]<role name …>` lines from @stept/dom-capture */
  elements: string;
  count: number;
  /** base64 JPEG, viewport-clipped, longest edge ≤ 1568 */
  screenshot?: string;
  screenshotSize?: { w: number; h: number };
  note?: string;
  pageText?: string;
  found?: Array<{ index: number; text: string; tag: string; visible: boolean }>;
  extracted?: { kind: string; value: string };
  console?: Array<{ level: string; text: string; t: number }>;
  network?: Array<{ method: string; url: string; status?: number; t: number }>;
}

/** Backend gateway → extension (WS /ws/extension). snake_case on the wire. */
export type GatewayToExtension =
  | { type: 'pong' }
  | { type: 'exec-op'; ctrl_id: string; op: DriveOp['op']; args?: DriveOp['args'] }
  | { type: 'record-start'; ctrl_id: string; url?: string }
  | { type: 'record-stop'; ctrl_id: string; title: string; description?: string }
  | { type: 'run-tour'; ctrl_id: string; tour_id: string; mode: 'driven' };

/** Extension → backend gateway. */
export type ExtensionToGateway =
  | { type: 'ping' }
  | { type: 'exec-result'; ctrl_id: string; ok: boolean; data?: DriveSnapshot; error?: string }
  | {
      type: 'record-ack';
      ctrl_id: string;
      ok: boolean;
      recording?: boolean;
      tour_id?: string;
      event_count?: number;
      error?: string;
    }
  | {
      type: 'run-result';
      ctrl_id: string;
      status: 'completed' | 'failed' | 'cancelled';
      error?: string;
    };

/** Remote-drive session surfaced in the side panel. */
export interface RemoteDriveState {
  tabId: number;
  url: string;
  opCount: number;
  startedAt: number;
}

// ---------------------------------------------------------------------------
// panel state (background-owned, broadcast on every change)
// ---------------------------------------------------------------------------

/** A tour pulled from the workspace for editing in the panel. */
export interface EditingTour {
  tourId: string;
  name: string;
  /** the version the deck was pulled at — sent as `base_version` on push */
  baseVersion: number;
  steps: TourStep[];
  status: string;
  /** set when a push 409'd: the dashboard changed the tour under us */
  conflict: boolean;
}

export interface SavedInfo {
  tourId: string;
  name: string;
  appUrl: string;
}

export interface PickedSelector {
  selector: string;
  fallbacks: string[];
  textHint: string;
}

export interface PanelState {
  auth: AuthState;
  recording: boolean;
  paused: boolean;
  /** capture a DOM replica per step so the tour can be replayed in a sandbox */
  sandbox: boolean;
  /** replicas captured this session, and their total size (panel warns near the cap) */
  sandboxStats: { captured: number; bytes: number };
  events: RawEvent[];
  startedAt: number | null;
  startUrl: string | null;
  /** stepId → user-edited title / body (survive recompiles: ids are stable) */
  titleOverrides: Record<string, string>;
  bodyOverrides: Record<string, string>;
  /** desired compiled-step order by id; ids absent here keep natural order */
  stepOrder: string[];
  saving: boolean;
  lastSave: SavedInfo | null;
  tours: TourSummary[];
  toursLoading: boolean;
  editing: EditingTour | null;
  guide: GuideState | null;
  drive: DriveState | null;
  /** last capture from the selector picker (copyable in the panel) */
  picked: PickedSelector | null;
  /** "Let Stept control this browser" — gates the whole remote-drive transport */
  remoteControl: boolean;
  /** live WS to the backend gateway right now */
  remoteConnected: boolean;
  /** an MCP client is currently driving a tab (null when idle) */
  remoteDrive: RemoteDriveState | null;
}

export function emptyPanelState(apiBase = DEFAULT_API_BASE): PanelState {
  return {
    auth: {
      signedIn: false,
      apiBase,
      workspaceId: null,
      workspaceName: null,
      userName: null,
      error: null,
      checking: false,
    },
    recording: false,
    paused: false,
    sandbox: false,
    sandboxStats: { captured: 0, bytes: 0 },
    events: [],
    startedAt: null,
    startUrl: null,
    titleOverrides: {},
    bodyOverrides: {},
    stepOrder: [],
    saving: false,
    lastSave: null,
    tours: [],
    toursLoading: false,
    editing: null,
    guide: null,
    drive: null,
    picked: null,
    remoteControl: true,
    remoteConnected: false,
    remoteDrive: null,
  };
}
