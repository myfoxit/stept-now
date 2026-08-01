import type { PageSnapshot } from '@stept/dom-capture';
import {
  ApiError,
  ConflictError,
  DapClient,
  login as apiLogin,
  mintExtensionToken,
  normalizeBase,
  tourAppUrl,
} from '../api/client';
import { clearSession, loadApiBase, loadSession, saveApiBase, saveSession } from '../api/session';
import { CaptureHold } from '../capture-hold';
import { compile } from '../compiler';
import { DriveRunner, type DriveDecision } from '../driver/runner';
import { buildGuidePayload, panelSteps, urlEffectMatches } from '../guide/guide-core';
import type {
  BgToGuideContent,
  BgToPanel,
  ContentToBg,
  PanelToBg,
  SignInResult,
  SimpleResult,
} from '../messages';
import {
  emptyPanelState,
  type DriveStepStatus,
  type PanelState,
  type RawEvent,
  type StoredSession,
  type TourStep,
  type WorkspaceChoice,
} from '../types';
import { isInternalUrl } from '../url-pattern';

const KEEPALIVE_ALARM = 'stept-keepalive';
const SCREENSHOT_REUSE_MS = 500;
const PRE_CAPTURE_REUSE_MS = 350;

/**
 * The service worker: session anchor, message router, capture pipeline, save
 * pipeline, and the guide + drive engines.
 *
 * MV3 tears a worker down ~30s after it goes idle, so ALL live state is
 * journaled to `chrome.storage.session` (`restore()` rebuilds it on wake) and a
 * keepalive alarm revives the worker while a recording or a run is in flight.
 * Structure ported from the old repo's `background.ts`; the API layer is new.
 */
export default defineBackground(() => {
  chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

  const state: PanelState = emptyPanelState();
  let session: StoredSession | null = null;
  let recordedTabIds = new Set<number>();
  let lastActiveTabId: number | null = null;
  /** Only between "sign in" and "choose workspace" — MEMORY ONLY, never
   * persisted, dropped the moment the extension token is minted. */
  let pendingLogin: { apiBase: string; accessToken: string; userName: string } | null = null;

  /** Full steps of the tour being guided/driven — too heavy to broadcast. */
  let guideSteps: TourStep[] = [];
  let driveSteps: TourStep[] = [];
  let driveRunner: DriveRunner | null = null;
  /** A navigation WE initiated for the guide: 'step' advances when it commits. */
  let guideAwaitingNav: 'step' | null = null;

  const api = new DapClient(
    () => session?.apiBase ?? state.auth.apiBase,
    () => session?.extensionToken ?? null,
  );

  // ---- state plumbing ----------------------------------------------------

  async function persist(): Promise<void> {
    await chrome.storage.session
      .set({
        panelstate: { ...state, events: state.events },
        rectabs: [...recordedTabIds],
        guidesteps: guideSteps,
        drivesteps: driveSteps,
      })
      .catch(() => {});
    broadcast();
  }

  function broadcast(): void {
    const msg: BgToPanel = { type: 'state', state };
    chrome.runtime.sendMessage(msg).catch(() => {});
  }

  function fail(message: string): void {
    const msg: BgToPanel = { type: 'error', message };
    chrome.runtime.sendMessage(msg).catch(() => {});
  }

  function applySession(next: StoredSession | null): void {
    session = next;
    state.auth = {
      signedIn: !!next,
      apiBase: next?.apiBase ?? state.auth.apiBase,
      workspaceId: next?.workspaceId ?? null,
      workspaceName: next?.workspaceName ?? null,
      userName: next?.userName ?? null,
      error: state.auth.error,
      checking: false,
    };
  }

  async function restore(): Promise<void> {
    const stored = await chrome.storage.session
      .get(['panelstate', 'rectabs', 'guidesteps', 'drivesteps'])
      .catch(() => ({}) as Record<string, unknown>);
    const saved = stored['panelstate'] as PanelState | undefined;
    if (saved) Object.assign(state, saved);
    if (Array.isArray(stored['rectabs'])) recordedTabIds = new Set(stored['rectabs'] as number[]);
    if (Array.isArray(stored['guidesteps'])) guideSteps = stored['guidesteps'] as TourStep[];
    if (Array.isArray(stored['drivesteps'])) driveSteps = stored['drivesteps'] as TourStep[];
    // a drive cannot survive a worker teardown (the CDP session went with it)
    if (state.drive && state.drive.status === 'running') {
      state.drive = { ...state.drive, status: 'error', error: 'The run was interrupted.' };
    }

    state.auth.apiBase = await loadApiBase();
    applySession(await loadSession());
    await persist();
    if (session) void validateSession();
  }

  /** Confirm the stored token still works. A revoked member (or a reset DB)
   * must surface as "sign in again", not as a dead save with no way forward. */
  async function validateSession(): Promise<void> {
    if (!session) return;
    state.auth.checking = true;
    broadcast();
    try {
      const check = await api.check();
      if (session) {
        session = {
          ...session,
          workspaceName: check.workspace_name,
          userName: check.user_name,
          appBaseUrl: check.app_base_url || session.appBaseUrl,
        };
        await saveSession(session);
        applySession(session);
      }
      state.auth.error = null;
      void refreshTours();
    } catch (err) {
      if (err instanceof ApiError && err.unauthorized) {
        await signOut('Your Stept session expired — sign in again to keep recording.');
        return;
      }
      // merely offline: stay signed in, the next real call re-validates
    } finally {
      state.auth.checking = false;
      await persist();
    }
  }

  async function signOut(reason: string | null = null): Promise<void> {
    await clearSession();
    applySession(null);
    state.auth.error = reason;
    state.tours = [];
    state.editing = null;
    await stopGuide();
    stopDrive();
    await persist();
  }

  /** Every API call funnels through here so a 401 always ends in a clean
   * signed-out state instead of a silent failure. */
  async function guarded<T>(fn: () => Promise<T>): Promise<T | null> {
    try {
      return await fn();
    } catch (err) {
      if (err instanceof ConflictError) throw err;
      if (err instanceof ApiError && err.unauthorized) {
        await signOut('Your Stept session expired — sign in again to keep recording.');
        return null;
      }
      fail(err instanceof Error ? err.message : String(err));
      return null;
    }
  }

  void restore();

  // Keepalive: an idle MV3 worker is torn down, which would strand a recording
  // mid-flight. A periodic alarm revives it (re-running this script → restore()).
  chrome.alarms.onAlarm.addListener((a) => {
    if (a.name !== KEEPALIVE_ALARM) return;
    if (!state.recording && !state.guide && !state.drive) {
      chrome.alarms.clear(KEEPALIVE_ALARM);
      return;
    }
    void persist();
  });

  function armKeepalive(): void {
    chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: 0.5 });
  }

  chrome.tabs.onActivated.addListener((info) => {
    lastActiveTabId = info.tabId;
  });

  chrome.commands?.onCommand?.addListener((command) => {
    if (command !== 'toggle-recording') return;
    if (state.recording) void stopRecording();
    else void startRecording();
  });

  async function activeTab(): Promise<number | null> {
    if (lastActiveTabId != null) {
      try {
        await chrome.tabs.get(lastActiveTabId);
        return lastActiveTabId;
      } catch {
        lastActiveTabId = null;
      }
    }
    const focused = (await chrome.tabs.query({ active: true, lastFocusedWindow: true }))[0];
    if (focused?.id != null) {
      lastActiveTabId = focused.id;
      return focused.id;
    }
    return (await chrome.tabs.query({ active: true }))[0]?.id ?? null;
  }

  // ---- badge -------------------------------------------------------------

  function setBadge(text: string, colour = '#4f46e5'): void {
    void chrome.action.setBadgeBackgroundColor({ color: colour }).catch(() => {});
    void chrome.action.setBadgeText({ text }).catch(() => {});
  }

  function refreshBadge(): void {
    if (state.recording) {
      const count = compileCurrent().steps.length;
      setBadge(count ? String(count) : '●', state.paused ? '#a16207' : '#dc2626');
    } else if (state.drive && state.drive.status !== 'completed') {
      setBadge(String(Math.min(state.drive.index + 1, state.drive.total)));
    } else if (state.guide?.status === 'active') {
      setBadge(String(Math.min(state.guide.index + 1, state.guide.total)));
    } else {
      setBadge('');
    }
  }

  // ---- recording ---------------------------------------------------------

  function compileCurrent() {
    return compile(state.events, {
      startUrl: state.startUrl,
      titleOverrides: state.titleOverrides,
      bodyOverrides: state.bodyOverrides,
      order: state.stepOrder,
    });
  }

  function setContentRecording(recording: boolean): void {
    for (const tabId of recordedTabIds) {
      chrome.tabs
        .sendMessage(tabId, { type: 'set-recording', recording, sandbox: state.sandbox })
        .catch(() => {});
    }
  }

  async function startRecording(tabId?: number): Promise<void> {
    if (!session) return;
    if (state.guide) await stopGuide();
    stopDrive();
    const id = tabId ?? (await activeTab());
    if (id == null) return;
    const tab = await chrome.tabs.get(id).catch(() => null);
    if (!tab?.id) return;
    recordedTabIds = new Set([tab.id]);
    tabShots.clear();
    captureHold.clear();
    pendingSnapshots.clear();
    Object.assign(state, {
      recording: true,
      paused: false,
      sandboxStats: { captured: 0, bytes: 0 },
      events: [],
      titleOverrides: {},
      bodyOverrides: {},
      stepOrder: [],
      startedAt: Date.now(),
      // pendingUrl: the committed url is still the OLD page at this instant
      startUrl: tab.pendingUrl ?? tab.url ?? null,
      lastSave: null,
      editing: null,
    });
    // the recorder is a manifest content script, but pages opened before
    // install (or after an extension reload) do not have it — inject defensively
    await chrome.scripting
      .executeScript({ target: { tabId: tab.id, allFrames: true }, files: ['content-scripts/recorder.js'] })
      .catch(() => {});
    setContentRecording(true);
    armKeepalive();
    refreshBadge();
    await persist();
  }

  async function stopRecording(): Promise<void> {
    state.recording = false;
    setContentRecording(false);
    refreshBadge();
    await persist();
  }

  // ---- screenshots: throttled ≤2/s (Chrome quota), uploaded eagerly -------

  /** Last uploaded screenshot PER TAB. A single global ref serves stale frames
   * across tabs the moment a click opens a new window, so per-tab is
   * load-bearing, not a nicety. */
  const tabShots = new Map<number, { at: number; key: string }>();
  const captureHold = new CaptureHold();
  /** In-flight pre-capture uploads by token — addEvent awaits its OWN token so
   * a click never races its pre-capture into the post-click fallback. */
  const pendingPreCaptures = new Map<string, Promise<void>>();

  /** Shoot the window the tab actually lives in — after a click opens a new
   * window, capturing "the current window" returns frames of the WRONG page. */
  async function captureAndUpload(tabId: number): Promise<string | null> {
    if (!session) return null;
    let windowId: number | undefined;
    try {
      windowId = (await chrome.tabs.get(tabId)).windowId;
    } catch {
      /* tab gone — fall back to the current window */
    }
    // quality 92: these are the tour's step images, not just replay context
    const dataUrl =
      windowId != null
        ? await chrome.tabs.captureVisibleTab(windowId, { format: 'jpeg', quality: 92 })
        : await chrome.tabs.captureVisibleTab({ format: 'jpeg', quality: 92 });
    const blob = await (await fetch(dataUrl)).blob();
    const key = await api.uploadScreenshot(blob, 'step.jpg');
    tabShots.set(tabId, { at: Date.now(), key });
    return key;
  }

  async function captureScreenshot(tabId: number): Promise<string | null> {
    const shot = tabShots.get(tabId);
    if (shot && Date.now() - shot.at < SCREENSHOT_REUSE_MS) return shot.key;
    try {
      return await captureAndUpload(tabId);
    } catch {
      return null;
    }
  }

  /** Screenshot NOW — the pointerdown moment, before click effects repaint.
   * Bypasses the reuse window (a pre-capture must be the CURRENT frame) but
   * still respects Chrome's rate limit by falling back to this tab's last shot
   * when captures come faster than the quota. */
  async function preCapture(tabId: number, token: string): Promise<void> {
    if (!state.recording || state.paused || !session) return;
    const shot = tabShots.get(tabId);
    if (shot && Date.now() - shot.at < PRE_CAPTURE_REUSE_MS) {
      captureHold.holdShot(tabId, shot.key, token);
      return;
    }
    const task = (async () => {
      try {
        const key = await captureAndUpload(tabId);
        if (key) captureHold.holdShot(tabId, key, token);
      } catch {
        /* quota exceeded or window gone — addEvent's fallback covers it */
      }
    })();
    pendingPreCaptures.set(token, task);
    await task.finally(() => pendingPreCaptures.delete(token));
  }

  // ---- sandbox replicas: uploaded per token, keyed like pre-captures ------

  /** In-flight replica uploads by token, plus the key each resolved to. Held by
   * TOKEN rather than by tab because a replica is pinned to the one gesture
   * that produced it — see CaptureHold for why token pairing is load-bearing. */
  const pendingSnapshots = new Map<string, Promise<string | null>>();
  const snapshotKeys = new Map<string, string>();

  function uploadSnapshot(token: string, snapshot: PageSnapshot): void {
    if (!state.recording || state.paused || !session || !state.sandbox) return;
    const task = (async (): Promise<string | null> => {
      try {
        const { key, bytes } = await api.uploadSnapshot(snapshot);
        snapshotKeys.set(token, key);
        state.sandboxStats = {
          captured: state.sandboxStats.captured + 1,
          bytes: state.sandboxStats.bytes + bytes,
        };
        return key;
      } catch {
        // Over the size cap, offline, or token revoked. A step without a
        // replica still plays from its screenshot — never break the recording.
        return null;
      }
    })();
    pendingSnapshots.set(token, task);
    void task.finally(() => {
      pendingSnapshots.delete(token);
      // The map is only ever read by the event carrying this token, which has
      // long since arrived; anything older is an abandoned pointerdown.
      if (snapshotKeys.size > 50) snapshotKeys.clear();
    });
  }

  const SHOTWORTHY = new Set(['pointer', 'select', 'check', 'upload', 'hover', 'input']);

  async function addEvent(event: RawEvent, captureToken?: string): Promise<void> {
    if (!state.recording || state.paused) return;
    if (captureToken && !event.sandboxKey) {
      await pendingSnapshots.get(captureToken)?.catch(() => null);
      const key = snapshotKeys.get(captureToken);
      if (key) {
        event.sandboxKey = key;
        snapshotKeys.delete(captureToken);
      }
    }
    if (SHOTWORTHY.has(event.kind) && !event.screenshotKey) {
      // prefer the pointerdown-time pre-capture: it shows the page the user
      // acted ON, not the state after the click's effects. The token pairs the
      // shot with ITS event — an interleaved input flush can't steal it.
      if (captureToken) await pendingPreCaptures.get(captureToken)?.catch(() => {});
      const held = captureHold.takeShot(event.tabId, captureToken);
      const key = held ?? (await captureScreenshot(event.tabId));
      if (key) event.screenshotKey = key;
    }
    state.events.push(event);
    refreshBadge();
    await persist();
  }

  // ---- background-observed events: navigation, downloads, tabs -----------

  chrome.webNavigation?.onCommitted?.addListener((details) => {
    if (details.frameId === 0) {
      // a committed navigation makes this tab's last screenshot a picture of a
      // page that no longer exists — never reuse it as "fresh"
      tabShots.delete(details.tabId);
      captureHold.clearTab(details.tabId);
    }
    if (!state.recording || state.paused || details.frameId !== 0) return;
    if (!recordedTabIds.has(details.tabId)) return;
    if (isInternalUrl(details.url)) return;
    const t = details.transitionType;
    // Address-bar navigations (typed URL, bookmark, search) are all deliberate.
    const transitionType =
      t === 'typed' || t === 'auto_bookmark' || t === 'generated' || t === 'keyword' || t === 'start_page'
        ? 'typed'
        : t === 'link'
          ? 'link'
          : 'auto';
    const qualifiers = (details as { transitionQualifiers?: string[] }).transitionQualifiers ?? [];
    const redirect = qualifiers.includes('server_redirect') || qualifiers.includes('client_redirect');
    // the SPA watcher in the content script may emit the same URL — dedupe here
    const last = [...state.events].reverse().find((e) => e.kind === 'nav');
    if (last && last.kind === 'nav' && last.url === details.url && Date.now() - last.t < 1500) return;
    state.events.push({
      kind: 'nav',
      t: Date.now(),
      tabId: details.tabId,
      frameId: 0,
      url: details.url,
      transitionType,
      redirect,
    });
    void persist();
  });

  chrome.downloads?.onCreated?.addListener((item) => {
    if (!state.recording || state.paused) return;
    state.events.push({
      kind: 'download',
      t: Date.now(),
      tabId: 0,
      frameId: 0,
      filename: item.filename || item.finalUrl || 'download',
      mime: item.mime,
    });
    void persist();
  });

  // multi-tab: follow tabs opened from a recorded tab (target=_blank, window.open)
  chrome.tabs.onCreated.addListener((tab) => {
    if (!state.recording || state.paused || tab.id == null) return;
    if (tab.openerTabId != null && recordedTabIds.has(tab.openerTabId)) {
      recordedTabIds.add(tab.id);
      state.events.push({
        kind: 'tab',
        t: Date.now(),
        tabId: tab.id,
        frameId: 0,
        action: 'created',
        url: tab.pendingUrl ?? tab.url,
      });
      void persist();
    }
  });

  chrome.tabs.onRemoved.addListener((tabId) => {
    tabShots.delete(tabId);
    captureHold.clearTab(tabId);
    if (recordedTabIds.delete(tabId) && state.recording) {
      state.events.push({ kind: 'tab', t: Date.now(), tabId, frameId: 0, action: 'closed' });
      void persist();
    }
    if (state.guide?.tabId === tabId && state.guide.status === 'active') {
      state.guide = { ...state.guide, status: 'error', error: 'The guided tab was closed.' };
      refreshBadge();
      void persist();
    }
    if (state.drive?.tabId === tabId) stopDrive('The tab being driven was closed.');
  });

  // ---- message routing ---------------------------------------------------

  chrome.runtime.onMessage.addListener(
    (msg: ContentToBg | PanelToBg, sender, sendResponse: (r: unknown) => void) => {
      if (!msg || typeof (msg as { type?: unknown }).type !== 'string') return;
      switch (msg.type) {
        case 'event': {
          if (sender.tab?.id != null && !recordedTabIds.has(sender.tab.id)) return;
          const ev = msg.event;
          if (sender.tab?.id != null) ev.tabId = sender.tab.id;
          if (sender.frameId != null) ev.frameId = sender.frameId;
          void addEvent(ev, msg.captureToken);
          return;
        }
        case 'content-ready':
          if (state.recording && sender.tab?.id != null && recordedTabIds.has(sender.tab.id)) {
            chrome.tabs.sendMessage(sender.tab.id, { type: 'set-recording', recording: true }).catch(() => {});
          }
          return;
        case 'pre-capture':
          if (sender.tab?.id != null && recordedTabIds.has(sender.tab.id)) {
            void preCapture(sender.tab.id, msg.token);
          }
          return;
        case 'snapshot':
          if (sender.tab?.id != null && recordedTabIds.has(sender.tab.id)) {
            uploadSnapshot(msg.token, msg.snapshot);
          }
          return;
        case 'guide-event':
          void onGuideEvent(msg.event, msg.index, sender.tab?.id);
          return;
        case 'picked':
          state.picked = msg.picked;
          void persist();
          return;
        default:
          return handlePanel(msg as PanelToBg, sendResponse);
      }
    },
  );

  function handlePanel(msg: PanelToBg, sendResponse: (r: unknown) => void): boolean | undefined {
    switch (msg.type) {
      case 'get-state':
        sendResponse({ type: 'state', state });
        return false;
      case 'sign-in':
        void signIn(msg.apiBase, msg.email, msg.password).then(sendResponse);
        return true;
      case 'choose-workspace':
        void chooseWorkspace(msg.workspaceId).then(sendResponse);
        return true;
      case 'adopt-token':
        void adoptToken(msg.apiBase, msg.token).then(sendResponse);
        return true;
      case 'sign-out':
        void signOut().then(() => sendResponse({ ok: true }));
        return true;
      case 'refresh-tours':
        void refreshTours().then(() => sendResponse({ ok: true }));
        return true;
      case 'start-recording':
        void startRecording(msg.tabId);
        return false;
      case 'stop-recording':
        void stopRecording();
        return false;
      case 'pause-recording':
        state.paused = msg.paused;
        refreshBadge();
        void persist();
        return false;
      case 'set-sandbox':
        state.sandbox = msg.sandbox;
        // Live-toggle: the content scripts learn on the next broadcast, so a
        // recording already in progress starts (or stops) capturing replicas
        // without being restarted.
        if (state.recording) setContentRecording(true);
        void persist();
        return false;
      case 'delete-events':
        for (const index of [...msg.indexes].sort((a, b) => b - a)) state.events.splice(index, 1);
        refreshBadge();
        void persist();
        return false;
      case 'retitle-step':
        if (msg.title.trim()) state.titleOverrides[msg.stepId] = msg.title.trim();
        else delete state.titleOverrides[msg.stepId];
        void persist();
        return false;
      case 'set-step-body':
        state.bodyOverrides[msg.stepId] = msg.body;
        void persist();
        return false;
      case 'reorder-steps':
        state.stepOrder = msg.order;
        void persist();
        return false;
      case 'discard-recording':
        Object.assign(state, {
          recording: false,
          paused: false,
          events: [],
          titleOverrides: {},
          bodyOverrides: {},
          stepOrder: [],
          startedAt: null,
          startUrl: null,
        });
        recordedTabIds.clear();
        refreshBadge();
        void persist();
        return false;
      case 'save-tour':
        void saveTour(msg.name, msg.urlPattern).then(sendResponse);
        return true;
      case 'pull-tour':
        void pullTour(msg.tourId).then(sendResponse);
        return true;
      case 'close-editing':
        state.editing = null;
        void persist();
        return false;
      case 'edit-step':
        if (state.editing) {
          state.editing = {
            ...state.editing,
            steps: state.editing.steps.map((s) => (s.id === msg.stepId ? { ...s, ...msg.patch } : s)),
          };
          void persist();
        }
        return false;
      case 'edit-delete-step':
        if (state.editing) {
          state.editing = {
            ...state.editing,
            steps: state.editing.steps.filter((s) => s.id !== msg.stepId),
          };
          void persist();
        }
        return false;
      case 'edit-move-step':
        if (state.editing) {
          const steps = [...state.editing.steps];
          const i = steps.findIndex((s) => s.id === msg.stepId);
          const j = i + msg.dir;
          if (i >= 0 && j >= 0 && j < steps.length) {
            const a = steps[i];
            const b = steps[j];
            if (a && b) {
              steps[i] = b;
              steps[j] = a;
              state.editing = { ...state.editing, steps };
              void persist();
            }
          }
        }
        return false;
      case 'push-tour':
        void pushTour().then(sendResponse);
        return true;
      case 'guide-start':
        void startGuide(msg.tourId).then(sendResponse);
        return true;
      case 'guide-stop':
        void stopGuide().then(() => sendResponse({ ok: true }));
        return true;
      case 'guide-nav':
        void guideAdvance(msg.dir);
        return false;
      case 'drive-start':
        void startDrive(msg.tourId).then(sendResponse);
        return true;
      case 'drive-stop':
        stopDrive();
        return false;
      case 'drive-pause':
        if (state.drive && driveRunner) {
          driveRunner.setPaused(msg.paused);
          state.drive = { ...state.drive, status: msg.paused ? 'paused' : 'running' };
          void persist();
        }
        return false;
      case 'drive-speed':
        if (state.drive && driveRunner) {
          driveRunner.setSpeed(msg.speed);
          state.drive = { ...state.drive, speed: msg.speed };
          void persist();
        }
        return false;
      case 'drive-decide':
        driveRunner?.resolveDecision(msg.decision as DriveDecision);
        return false;
      case 'picker-start':
        void startPicker();
        return false;
      case 'clear-picked':
        state.picked = null;
        void persist();
        return false;
    }
    return false;
  }

  // ---- auth flows --------------------------------------------------------

  async function signIn(apiBaseRaw: string, email: string, password: string): Promise<SignInResult> {
    const apiBase = normalizeBase(apiBaseRaw);
    state.auth.apiBase = apiBase;
    await saveApiBase(apiBase);
    try {
      const outcome = await apiLogin(apiBase, email, password);
      const usable = outcome.workspaces.filter((w) => w.canManageTours);
      if (usable.length === 0) {
        return {
          ok: false,
          error:
            outcome.workspaces.length === 0
              ? 'This account is not a member of any workspace yet.'
              : 'None of your workspaces grant the "tours:manage" permission.',
        };
      }
      pendingLogin = { apiBase, accessToken: outcome.accessToken, userName: outcome.userName };
      if (usable.length === 1) {
        const only = usable[0] as WorkspaceChoice;
        const done = await chooseWorkspace(only.id, only.name);
        return done.ok ? { ok: true } : { ok: false, error: done.error };
      }
      return { ok: true, workspaces: usable };
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? 'Wrong email or password.'
          : err instanceof Error
            ? err.message
            : String(err);
      return { ok: false, error: message };
    }
  }

  /** Mint the long-lived extension token, then DROP the access token. */
  async function chooseWorkspace(workspaceId: string, workspaceName?: string): Promise<SimpleResult> {
    if (!pendingLogin) return { ok: false, error: 'Sign in again — the login session expired.' };
    const { apiBase, accessToken, userName } = pendingLogin;
    try {
      const token = await mintExtensionToken(apiBase, accessToken, workspaceId);
      pendingLogin = null; // the access token dies here, and is never persisted
      const next: StoredSession = {
        apiBase,
        extensionToken: token,
        workspaceId,
        workspaceName: workspaceName ?? '',
        userName,
        appBaseUrl: '', // filled in by the validateSession() call below
      };
      await saveSession(next);
      applySession(next);
      state.auth.error = null;
      await persist();
      void validateSession();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }

  /** Advanced fallback: paste an extension (or legacy recorder) token. */
  async function adoptToken(apiBaseRaw: string, token: string): Promise<SimpleResult> {
    const apiBase = normalizeBase(apiBaseRaw);
    const probe: StoredSession = {
      apiBase,
      extensionToken: token.trim(),
      workspaceId: 'pending',
      workspaceName: '',
      userName: '',
      appBaseUrl: '',
    };
    const previous = session;
    session = probe;
    try {
      const check = await api.check();
      const next: StoredSession = {
        apiBase,
        extensionToken: probe.extensionToken,
        workspaceId: check.workspace_id,
        workspaceName: check.workspace_name,
        userName: check.user_name,
        appBaseUrl: check.app_base_url || '',
      };
      await saveApiBase(apiBase);
      await saveSession(next);
      applySession(next);
      state.auth.error = null;
      await persist();
      void refreshTours();
      return { ok: true };
    } catch (err) {
      session = previous;
      return {
        ok: false,
        error: err instanceof ApiError && err.unauthorized ? 'That token is not valid for this Stept instance.' : String(err),
      };
    }
  }

  // ---- tours -------------------------------------------------------------

  async function refreshTours(): Promise<void> {
    if (!session) return;
    state.toursLoading = true;
    broadcast();
    const tours = await guarded(() => api.listTours());
    state.toursLoading = false;
    if (tours) state.tours = tours;
    await persist();
  }

  async function saveTour(name: string, urlPattern?: string): Promise<SimpleResult> {
    if (!session) return { ok: false, error: 'Sign in first.' };
    if (state.saving) return { ok: false, error: 'A save is already in progress.' };
    const compiled = compileCurrent();
    if (!compiled.steps.length) return { ok: false, error: 'Nothing recorded yet.' };
    state.saving = true;
    await persist();
    try {
      const tour = await guarded(() => api.createTour(name, compiled.steps, urlPattern));
      if (!tour) return { ok: false, error: 'Save failed.' };
      Object.assign(state, {
        recording: false,
        events: [],
        titleOverrides: {},
        bodyOverrides: {},
        stepOrder: [],
        startedAt: null,
        lastSave: {
          tourId: tour.id,
          name: tour.name,
          appUrl: tourAppUrl(session.appBaseUrl || session.apiBase, tour.id),
        },
      });
      recordedTabIds.clear();
      void refreshTours();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    } finally {
      state.saving = false;
      refreshBadge();
      await persist();
    }
  }

  async function pullTour(tourId: string): Promise<SimpleResult> {
    const tour = await guarded(() => api.getTour(tourId));
    if (!tour) return { ok: false, error: 'Could not load that tour.' };
    state.editing = {
      tourId: tour.id,
      name: tour.name,
      baseVersion: tour.version,
      steps: tour.steps,
      status: tour.status,
      conflict: false,
    };
    await persist();
    return { ok: true };
  }

  async function pushTour(): Promise<SimpleResult> {
    const editing = state.editing;
    if (!editing) return { ok: false, error: 'Nothing to push.' };
    state.saving = true;
    await persist();
    try {
      const tour = await api.putSteps(editing.tourId, editing.steps, editing.baseVersion);
      state.editing = {
        ...editing,
        baseVersion: tour.version,
        steps: tour.steps,
        conflict: false,
      };
      void refreshTours();
      return { ok: true };
    } catch (err) {
      if (err instanceof ConflictError) {
        state.editing = { ...editing, conflict: true };
        return {
          ok: false,
          error: 'This tour changed in the dashboard while you were editing. Reload it to keep those changes.',
        };
      }
      if (err instanceof ApiError && err.unauthorized) {
        await signOut('Your Stept session expired — sign in again to keep editing.');
        return { ok: false, error: 'Signed out.' };
      }
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    } finally {
      state.saving = false;
      await persist();
    }
  }

  // ---- guide mode --------------------------------------------------------

  async function startGuide(tourId: string): Promise<SimpleResult> {
    if (state.recording) return { ok: false, error: 'Stop the recording before starting a guide.' };
    stopDrive();
    if (state.guide) await stopGuide();
    const tour = await guarded(() => api.getTour(tourId));
    if (!tour) return { ok: false, error: 'Could not load that tour.' };
    if (!tour.steps.length) return { ok: false, error: 'This tour has no steps yet.' };
    const tabId = await activeTab();
    if (tabId == null) return { ok: false, error: 'No tab to run the guide in.' };

    guideSteps = tour.steps;
    guideAwaitingNav = null;
    state.guide = {
      tourId: tour.id,
      name: tour.name,
      tabId,
      index: 0,
      total: tour.steps.length,
      steps: panelSteps(tour.steps),
      status: 'active',
      stuck: false,
      error: null,
    };
    armKeepalive();
    refreshBadge();
    await persist();
    await driveGuideStep();
    return { ok: true };
  }

  async function stopGuide(): Promise<void> {
    const guide = state.guide;
    if (guide) hideGuideOverlay(guide.tabId);
    state.guide = null;
    guideSteps = [];
    guideAwaitingNav = null;
    refreshBadge();
    await persist();
  }

  async function guideAdvance(dir: 1 | -1): Promise<void> {
    const guide = state.guide;
    if (!guide || guide.status !== 'active') return;
    const index = Math.max(0, guide.index + dir);
    guideAwaitingNav = null;
    if (index >= guide.total) {
      state.guide = { ...guide, status: 'completed', index: guide.total, stuck: false };
      hideGuideOverlay(guide.tabId);
      refreshBadge();
      await persist();
      return;
    }
    state.guide = { ...guide, index, stuck: false };
    refreshBadge();
    await persist();
    await driveGuideStep();
  }

  /** Present the current step: navigate/wait-for-url steps are performed FOR
   * the user; everything else is handed to the overlay to spotlight. */
  async function driveGuideStep(): Promise<void> {
    const guide = state.guide;
    if (!guide || guide.status !== 'active') return;
    const step = guideSteps[guide.index];
    if (!step) return;
    refreshBadge();

    if (step.type === 'action' && step.action?.kind === 'navigate' && step.action.url) {
      guideAwaitingNav = 'step';
      hideGuideOverlay(guide.tabId);
      const ok = await chrome.tabs
        .update(guide.tabId, { url: step.action.url })
        .then(() => true)
        .catch(() => false);
      if (!ok) {
        state.guide = { ...guide, status: 'error', error: 'The guided tab was closed.' };
        guideAwaitingNav = null;
        await persist();
      }
      return;
    }
    await sendGuideShow();
  }

  async function sendGuideShow(): Promise<void> {
    const guide = state.guide;
    if (!guide || guide.status !== 'active') return;
    const step = guideSteps[guide.index];
    if (!step) return;
    // the guide script is a manifest content script, but pages opened before
    // install (or chrome error pages) may not have it — inject defensively
    await chrome.scripting
      .executeScript({ target: { tabId: guide.tabId }, files: ['content-scripts/guide.js'] })
      .catch(() => {});
    const msg: BgToGuideContent = {
      type: 'guide-show',
      step: buildGuidePayload(step, guide.index, guide.total, guide.name),
    };
    chrome.tabs.sendMessage(guide.tabId, msg).catch(() => {
      /* content script not ready — onCompleted re-drives this step */
    });
  }

  function hideGuideOverlay(tabId: number): void {
    const msg: BgToGuideContent = { type: 'guide-hide' };
    chrome.tabs.sendMessage(tabId, msg).catch(() => {});
  }

  /** Overlay events, anchored to the step index they happened on — a message
   * from a page we already advanced past must not double-advance. */
  async function onGuideEvent(
    event: 'advance' | 'back' | 'stop' | 'notfound' | 'found',
    index: number,
    senderTabId?: number,
  ): Promise<void> {
    const guide = state.guide;
    if (!guide || guide.status !== 'active') return;
    if (senderTabId != null && senderTabId !== guide.tabId) return;
    if (event === 'stop') return stopGuide();
    if (index !== guide.index) return;
    if (event === 'advance') return guideAdvance(1);
    if (event === 'back') return guideAdvance(-1);
    state.guide = { ...guide, stuck: event === 'notfound' };
    await persist();
  }

  // Guided-tab navigation: our own navs advance/re-drive; the user's click that
  // navigated (whose "done" message can lose the unload race) advances via the
  // step's url effect.
  chrome.webNavigation?.onCommitted?.addListener((details) => {
    const guide = state.guide;
    if (!guide || guide.status !== 'active' || details.frameId !== 0 || details.tabId !== guide.tabId) return;
    if (guideAwaitingNav === 'step') {
      guideAwaitingNav = null;
      void guideAdvance(1);
      return;
    }
    const step = guideSteps[guide.index];
    if (step && urlEffectMatches(step, details.url)) void guideAdvance(1);
  });

  // The destination page finished loading (or an SPA rewrote its route):
  // re-present the current step there. Idempotent — the overlay tears down and
  // rebuilds the same step.
  chrome.webNavigation?.onCompleted?.addListener((details) => {
    const guide = state.guide;
    if (!guide || guide.status !== 'active' || details.frameId !== 0 || details.tabId !== guide.tabId) return;
    void sendGuideShow();
  });
  chrome.webNavigation?.onHistoryStateUpdated?.addListener((details) => {
    const guide = state.guide;
    if (!guide || guide.status !== 'active' || details.frameId !== 0 || details.tabId !== guide.tabId) return;
    void sendGuideShow();
  });

  // ---- drive mode --------------------------------------------------------

  async function startDrive(tourId: string): Promise<SimpleResult> {
    if (state.recording) return { ok: false, error: 'Stop the recording before driving a tour.' };
    if (state.guide) await stopGuide();
    stopDrive();
    const tour = await guarded(() => api.getTour(tourId));
    if (!tour) return { ok: false, error: 'Could not load that tour.' };
    if (!tour.steps.length) return { ok: false, error: 'This tour has no steps yet.' };
    const tabId = await activeTab();
    if (tabId == null) return { ok: false, error: 'No tab to drive.' };

    driveSteps = tour.steps;
    state.drive = {
      tourId: tour.id,
      name: tour.name,
      tabId,
      index: 0,
      total: tour.steps.length,
      steps: panelSteps(tour.steps),
      stepStatus: tour.steps.map((): DriveStepStatus => 'pending'),
      status: 'running',
      speed: 1,
      transport: 'synthetic',
      error: null,
      awaitingDecision: false,
    };
    armKeepalive();
    refreshBadge();
    await persist();

    const runner = new DriveRunner({
      tabId,
      steps: tour.steps,
      report: (patch) => {
        const drive = state.drive;
        if (!drive) return;
        const stepStatus = [...drive.stepStatus];
        const index = patch.index ?? drive.index;
        if (patch.stepStatus && index < stepStatus.length) stepStatus[index] = patch.stepStatus;
        state.drive = {
          ...drive,
          index,
          stepStatus,
          status: patch.status ?? drive.status,
          error: patch.error === undefined ? drive.error : patch.error,
          awaitingDecision: patch.awaitingDecision ?? drive.awaitingDecision,
          transport: patch.transport ?? drive.transport,
        };
        refreshBadge();
        void persist();
      },
    });
    driveRunner = runner;
    await runner.prepare();
    void runner.run().finally(() => {
      if (driveRunner === runner) driveRunner = null;
      refreshBadge();
      void persist();
    });
    return { ok: true };
  }

  function stopDrive(reason?: string): void {
    driveRunner?.stop();
    driveRunner = null;
    if (state.drive) {
      state.drive = reason
        ? { ...state.drive, status: 'error', error: reason, awaitingDecision: false }
        : null;
    }
    driveSteps = [];
    refreshBadge();
    void persist();
  }

  // ---- selector picker ---------------------------------------------------

  async function startPicker(): Promise<void> {
    const tabId = await activeTab();
    if (tabId == null) return;
    state.picked = null;
    await persist();
    await chrome.scripting
      .executeScript({ target: { tabId }, files: ['content-scripts/picker.js'] })
      .catch(() => {});
    chrome.tabs.sendMessage(tabId, { type: 'picker-arm' }).catch(() => {});
  }
});
