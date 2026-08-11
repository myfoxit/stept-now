/**
 * The trusted-input path for drive mode: a `chrome.debugger` CDP session.
 *
 * This is the ONLY way an extension can dispatch input a page sees as
 * `isTrusted` — `el.click()` and synthetic KeyboardEvents are visibly fake to
 * any app that checks, and React's synthetic-event layer swallows a naive
 * `value =` assignment. Ported (subset) from the old repo's
 * `executor-extension.ts`: attach/reattach, `Input.dispatchMouseEvent` clicks,
 * `Input.insertText` typing, `Input.dispatchKeyEvent` keys, wheel scrolling.
 *
 * Attaching shows Chrome's "started debugging this browser" banner. If the user
 * refuses (or another debugger already owns the tab), `attach` returns null and
 * the runner falls back to the synthetic executor in the driver island.
 */

import { jpegSize } from '../remote/coord';

const CDP_TIMEOUT_MS = 15_000;
/** Hard cap on a `Page.navigate` load wait — a dead origin must not hang an op. */
const NAVIGATE_TIMEOUT_MS = 15_000;

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), ms)),
  ]);
}

/** Key metadata for the non-printable keys drive mode presses. */
const KEY_DEFS: Record<string, Record<string, unknown>> = {
  Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
  Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 },
  Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 },
  Backspace: { key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 },
  Delete: { key: 'Delete', code: 'Delete', windowsVirtualKeyCode: 46 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38 },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37 },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', windowsVirtualKeyCode: 39 },
  Home: { key: 'Home', code: 'Home', windowsVirtualKeyCode: 36 },
  End: { key: 'End', code: 'End', windowsVirtualKeyCode: 35 },
};

export class CdpSession {
  private attached = false;

  private constructor(private readonly tabId: number) {}

  /** Attach + enable the domains we drive. Returns null when the user (or
   * another debugger client) refuses — the caller degrades to synthetic input
   * rather than failing the run. */
  static async attach(tabId: number): Promise<CdpSession | null> {
    const session = new CdpSession(tabId);
    try {
      await session.doAttach();
      return session;
    } catch {
      return null;
    }
  }

  private async doAttach(): Promise<void> {
    // detach-first for a clean slate (clears a stale attach from a prior run)
    await chrome.debugger.detach({ tabId: this.tabId }).catch(() => {});
    await chrome.debugger.attach({ tabId: this.tabId }, '1.3');
    this.attached = true;
    // NB: we deliberately do NOT focus or activate the tab — trusted CDP
    // Input.* lands on unfocused tabs, and activating would steal the window.
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Page.enable', {}).catch(() => {});
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Runtime.enable', {}).catch(() => {});
    // Console + network are enabled UP FRONT, not on first read: their value is
    // the history leading up to a problem ("what did the page log when I clicked
    // that?"), and a domain enabled only at read time has no history to give.
    // Both are best-effort — a page that refuses them must not kill the session.
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Log.enable', {}).catch(() => {});
    await chrome.debugger.sendCommand({ tabId: this.tabId }, 'Network.enable', {}).catch(() => {});
    this.installTelemetryListener();
  }

  // -------------------------------------------------------------------------
  // Console / network telemetry — ring buffers, not logs: a chatty page emits
  // thousands of entries and the caller only ever wants the recent tail.
  // Bounded so a long remote-drive session can never grow the service worker's
  // heap without limit. Armed on attach (see doAttach), torn down in detach().
  // -------------------------------------------------------------------------

  private consoleBuf: ConsoleRow[] = [];
  private networkBuf: NetworkRow[] = [];
  private telemetryListener:
    | ((source: chrome.debugger.Debuggee, method: string, params?: object) => void)
    | null = null;

  /** ONE listener per session. A reattach re-installs (removing the old one)
   * rather than stacking a second — double-counting every console line. */
  private installTelemetryListener(): void {
    if (this.telemetryListener) chrome.debugger.onEvent.removeListener(this.telemetryListener);
    this.telemetryListener = (source, method, params) => {
      if (source.tabId !== this.tabId || !params) return;
      recordTelemetryEvent(
        { console: this.consoleBuf, network: this.networkBuf },
        method,
        params as Record<string, unknown>,
        Date.now(),
      );
    };
    chrome.debugger.onEvent.addListener(this.telemetryListener);
  }

  private async cdp<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    retry = 0,
  ): Promise<T> {
    try {
      return (await withTimeout(
        chrome.debugger.sendCommand({ tabId: this.tabId }, method, params),
        CDP_TIMEOUT_MS,
        `CDP ${method}`,
      )) as T;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // A cross-document navigation or a service-worker restart detaches the
      // debugger mid-run; the next command then throws "not attached".
      // Reattach once and retry — the trick that survives SPA navigations.
      if (retry < 1 && /not attached|detached while handling/i.test(message)) {
        this.attached = false;
        await this.doAttach().catch(() => {});
        if (this.attached) return this.cdp<T>(method, params, retry + 1);
      }
      throw err;
    }
  }

  /** Hover first (so hover-reveal menus open), then press/release. */
  async click(x: number, y: number, button: 'left' | 'middle' | 'right' = 'left', clickCount = 1): Promise<void> {
    const buttons = button === 'left' ? 1 : button === 'right' ? 2 : 4;
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none', buttons: 0 });
    for (let n = 1; n <= clickCount; n++) {
      await this.cdp('Input.dispatchMouseEvent', {
        type: 'mousePressed',
        x,
        y,
        button,
        buttons,
        clickCount: n,
        force: 0.5,
      });
      await this.cdp('Input.dispatchMouseEvent', {
        type: 'mouseReleased',
        x,
        y,
        button,
        buttons: 0,
        clickCount: n,
      });
    }
  }

  async hover(x: number, y: number): Promise<void> {
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  }

  /** Trusted text entry. `Input.insertText` is one atomic insertion — fast and
   * React-safe. Canvas/rich editors that only listen to key events get the
   * per-character path instead. */
  async insertText(text: string, perCharacter = false): Promise<void> {
    if (!perCharacter) {
      await this.cdp('Input.insertText', { text });
      return;
    }
    for (const ch of text) {
      await this.cdp('Input.dispatchKeyEvent', { type: 'keyDown', key: ch, text: ch, unmodifiedText: ch });
      await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
  }

  async pressKey(key: string): Promise<void> {
    const defn = KEY_DEFS[key] ?? (key.length === 1
      ? { key, text: key, unmodifiedText: key }
      : { key, code: key });
    await this.cdp('Input.dispatchKeyEvent', {
      type: 'text' in defn && defn.text ? 'keyDown' : 'rawKeyDown',
      ...defn,
    });
    await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', ...defn });
  }

  async scrollBy(deltaX: number, deltaY: number, x = 10, y = 200): Promise<void> {
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX, deltaY });
  }

  /** Clear the field before typing into it (select-all + delete). */
  async clearField(): Promise<void> {
    const mod = navigator.userAgent.includes('Mac') ? 4 : 2; // Meta : Ctrl
    await this.cdp('Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      key: 'a',
      code: 'KeyA',
      windowsVirtualKeyCode: 65,
      modifiers: mod,
      ...(mod === 4 ? { commands: ['selectAll'] } : {}),
    });
    await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: mod });
    await this.pressKey('Backspace');
  }

  async detach(): Promise<void> {
    // Telemetry teardown must not hide behind the attached flag: a failed
    // mid-run reattach leaves `attached` false with the listener still armed.
    if (this.telemetryListener) {
      chrome.debugger.onEvent.removeListener(this.telemetryListener);
      this.telemetryListener = null;
    }
    this.consoleBuf = [];
    this.networkBuf = [];
    if (!this.attached) return;
    this.attached = false;
    await chrome.debugger.detach({ tabId: this.tabId }).catch(() => {});
  }

  // -------------------------------------------------------------------------
  // Remote-drive surface (docs/MCP-CONTRACTS.md): what the drive controller
  // needs beyond local drive — screenshots, page evaluation, navigation,
  // chords, drag, per-character typing, and console/network telemetry. The
  // local-drive methods above are untouched by any of it.
  // -------------------------------------------------------------------------

  /** Like `attach`, but a refusal THROWS: a remote caller must get the real
   * error over MCP, not a silent downgrade to synthetic input. */
  static async attachOrThrow(tabId: number): Promise<CdpSession> {
    const session = await CdpSession.attach(tabId);
    if (!session) {
      throw new Error(
        'Chrome refused the debugger attach on this tab (user declined, or another debugger owns it)',
      );
    }
    return session;
  }

  /** Vision budget: longest screenshot edge ≤ 1280 px. Snapshots ride MCP
   * responses into agent contexts, so smaller-but-legible beats pixel-perfect
   * (screenshots are opt-in per op on top of this). */
  private static readonly MAX_SHOT_DIM = 1280;

  /** Viewport-clipped base64 JPEG, longest edge ≤ 1280 px.
   *
   * `fromSurface:true` + `captureBeyondViewport:false` captures from the
   * compositor SURFACE, which reliably grabs a BACKGROUND/unfocused tab — the
   * normal case for a remotely driven tab; a plain capture can return a blank
   * or stale frame off-focus. The clip (always present, even at scale 1) makes
   * CDP treat devicePixelRatio as 1, so the image is in CSS-pixel space — on a
   * 2× display an unclipped capture is ~2× the CSS size and the caller's
   * coordinate clicks land in the wrong place. `scale` downscales server-side
   * when the viewport exceeds the vision budget; the returned w/h are measured
   * from the ACTUAL jpeg so coordinate mapping never trusts the request. */
  async screenshot(quality = 60): Promise<{ data: string; w: number; h: number }> {
    const params: Record<string, unknown> = {
      format: 'jpeg',
      quality,
      fromSurface: true,
      captureBeyondViewport: false,
    };
    let clipW = 0;
    let clipH = 0;
    let scale = 1;
    try {
      const metrics = await this.cdp<{
        cssVisualViewport?: { clientWidth?: number; clientHeight?: number };
      }>('Page.getLayoutMetrics', {});
      const vp = metrics.cssVisualViewport;
      if (vp && (vp.clientWidth ?? 0) > 0 && (vp.clientHeight ?? 0) > 0) {
        clipW = vp.clientWidth!;
        clipH = vp.clientHeight!;
        const longest = Math.max(clipW, clipH);
        scale = longest > CdpSession.MAX_SHOT_DIM ? CdpSession.MAX_SHOT_DIM / longest : 1;
        params.clip = { x: 0, y: 0, width: clipW, height: clipH, scale };
      }
    } catch {
      // no layout metrics (rare) — capture the plain viewport, sizes from the jpeg
    }
    const { data } = await this.cdp<{ data: string }>('Page.captureScreenshot', params);
    const dim = jpegSize(data);
    return {
      data,
      w: dim?.w ?? Math.round(clipW * scale),
      h: dim?.h ?? Math.round(clipH * scale),
    };
  }

  /** `Runtime.evaluate` with returnByValue + userGesture (so probes may call
   * gesture-gated APIs). A page-side exception surfaces as a THROWN Error — a
   * remote caller must see the real failure, not `undefined`. */
  async evaluate<T = unknown>(expression: string, awaitPromise = true): Promise<T> {
    const r = await this.cdp<{
      result?: { value?: unknown };
      exceptionDetails?: { text?: string; exception?: { description?: string } };
    }>('Runtime.evaluate', {
      expression,
      returnByValue: true,
      userGesture: true,
      awaitPromise,
      timeout: 10_000,
    });
    if (r.exceptionDetails) {
      throw new Error(
        r.exceptionDetails.exception?.description ?? r.exceptionDetails.text ?? 'evaluation failed',
      );
    }
    return r.result?.value as T;
  }

  /** CSS viewport size (innerWidth/innerHeight) — the denominator for mapping
   * SCREENSHOT pixels → CSS px (cssX = imgX·innerW/shotW). Robust to whether
   * the capture came back device- or CSS-sized, and to page zoom, unlike
   * dividing by devicePixelRatio. {0,0} is the "unknown" sentinel — the
   * coordinate map falls back to identity rather than throwing. */
  async viewportSize(): Promise<{ w: number; h: number }> {
    try {
      const v = await this.evaluate<{ w: number; h: number } | undefined>(
        '({ w: window.innerWidth, h: window.innerHeight })',
        false,
      );
      if (v && v.w > 0 && v.h > 0) return { w: v.w, h: v.h };
    } catch {
      // mid-navigation / crashed renderer — report unknown
    }
    return { w: 0, h: 0 };
  }

  /** `Page.navigate` + load wait. The listener is armed BEFORE the navigate is
   * sent so a fast (cached) load can't fire its event into the void; resolves
   * on `Page.loadEventFired` OR `Page.frameStoppedLoading` (SPAs and aborted
   * loads often emit only the latter), or the hard timeout — never rejects on
   * a slow page, the follow-up snapshot shows whatever state it reached. */
  async navigate(url: string): Promise<void> {
    const loaded = this.waitForLoad(NAVIGATE_TIMEOUT_MS);
    await this.cdp('Page.navigate', { url });
    await loaded;
  }

  private waitForLoad(timeoutMs: number): Promise<void> {
    return new Promise((resolve) => {
      const tabId = this.tabId;
      const timer = setTimeout(finish, timeoutMs);
      const onEvent = (source: chrome.debugger.Debuggee, method: string): void => {
        if (
          source.tabId === tabId &&
          (method === 'Page.loadEventFired' || method === 'Page.frameStoppedLoading')
        ) {
          finish();
        }
      };
      function finish(): void {
        clearTimeout(timer);
        chrome.debugger.onEvent.removeListener(onEvent);
        resolve();
      }
      chrome.debugger.onEvent.addListener(onEvent);
    });
  }

  /** Wait until the page is in a runnable/settled state, capped at `timeoutMs`.
   * Polls `document.readyState` via evaluate — mid-navigation the evaluate
   * throws or reports "loading", so polling until "complete" is a REAL
   * page-settled gate (unlike a blind timer) that also waits through an SPA or
   * full navigation. The poll itself is raced against a hard timer: a wedged
   * renderer can stall an evaluate indefinitely, and settling must never hang
   * a driven op. Never throws. */
  async awaitIdle(timeoutMs = 8000): Promise<void> {
    const poll = async (): Promise<void> => {
      const deadline = Date.now() + timeoutMs;
      for (;;) {
        let state = 'loading';
        try {
          state = await this.evaluate<string>('document.readyState', false);
        } catch {
          // frame is navigating / renderer busy — treat as not-idle and retry
        }
        if (state === 'complete') return;
        if (Date.now() >= deadline) return;
        await new Promise((r) => setTimeout(r, 120));
      }
    };
    await Promise.race([poll(), new Promise<void>((r) => setTimeout(r, timeoutMs + 500))]);
  }

  /** Press a key or chord: "Enter", "Meta+a", "Shift+ArrowLeft". Modifiers ride
   * the CDP `modifiers` bitmask and, on macOS, the matching NSResponder editing
   * command rides the keyDown — without it Chrome ignores Cmd shortcuts
   * entirely (cmd+a would not select). A single un-modified key goes through
   * the plain text-producing `pressKey` path. */
  async pressChord(chord: string): Promise<void> {
    const plan = planChord(chord);
    if (plan.single !== undefined) return this.pressKey(plan.single);
    const base: Record<string, unknown> = {
      key: plan.key,
      code: plan.code,
      modifiers: plan.modifiers,
    };
    if (plan.windowsVirtualKeyCode) base.windowsVirtualKeyCode = plan.windowsVirtualKeyCode;
    // With a modifier held the key is a shortcut, not text → rawKeyDown (no text).
    await this.cdp('Input.dispatchKeyEvent', {
      type: 'rawKeyDown',
      ...base,
      ...(plan.commands ? { commands: plan.commands } : {}),
    });
    await this.cdp('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
  }

  /** Press, move in steps, release. The intermediate moves are load-bearing:
   * HTML5 drag-and-drop and every canvas app track pointer deltas, so a
   * press-then-release with no motion between reads as a click, not a drag. */
  async drag(fromX: number, fromY: number, toX: number, toY: number): Promise<void> {
    const steps = 10;
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x: fromX, y: fromY, button: 'none', buttons: 0 });
    await this.cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x: fromX, y: fromY, button: 'left', buttons: 1, clickCount: 1 });
    for (let i = 1; i <= steps; i++) {
      const x = fromX + ((toX - fromX) * i) / steps;
      const y = fromY + ((toY - fromY) * i) / steps;
      await this.cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'left', buttons: 1 });
    }
    await this.cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x: toX, y: toY, button: 'left', buttons: 0, clickCount: 1 });
  }

  /** Type `value` the way a human would: one trusted keyDown+keyUp per character
   * carrying a real code/keyCode + text, so the page fires the full
   * keydown→keypress→input sequence. Unlike `insertText`, this lands in
   * focus-gated canvas editors (Google Docs) and triggers editor auto-format
   * ("* "→bullet). Newlines/tabs become real Enter/Tab presses; emoji/CJK/
   * accented chars have no physical key and fall back to `Input.insertText`
   * (the IME/composition path). */
  async typeText(text: string): Promise<void> {
    for (const ch of text) {
      if (ch === '\n' || ch === '\r') {
        await this.pressKey('Enter');
        continue;
      }
      if (ch === '\t') {
        await this.pressKey('Tab');
        continue;
      }
      const info = keyInfoForChar(ch);
      if (!info) {
        await this.cdp('Input.insertText', { text: ch });
        continue;
      }
      const modifiers = info.shift ? 8 : 0;
      await this.cdp('Input.dispatchKeyEvent', {
        type: 'keyDown',
        key: ch,
        code: info.code,
        windowsVirtualKeyCode: info.keyCode,
        text: ch,
        unmodifiedText: ch,
        modifiers,
      });
      await this.cdp('Input.dispatchKeyEvent', {
        type: 'keyUp',
        key: ch,
        code: info.code,
        windowsVirtualKeyCode: info.keyCode,
        modifiers,
      });
    }
  }

  /** Recent console output + page exceptions, newest last (ring cap 200, armed
   * on attach). `pattern` filters — a chatty app otherwise buries the one line
   * that matters. */
  consoleMessages(pattern?: string, limit?: number): Array<{ level: string; text: string; t: number }> {
    const rows = filterByPattern(this.consoleBuf, pattern, (r) => r.text);
    return rows.slice(-clampLimit(limit));
  }

  /** Recent network requests, newest last (ring cap 200, armed on attach). */
  networkRequests(
    pattern?: string,
    limit?: number,
  ): Array<{ method: string; url: string; status?: number; t: number }> {
    const rows = filterByPattern(this.networkBuf, pattern, (r) => r.url);
    return rows.slice(-clampLimit(limit)).map(({ method, url, status, t }) => ({ method, url, status, t }));
  }
}

// ---------------------------------------------------------------------------
// Pure helpers — exported so the chord/telemetry/filter contracts unit-test in
// vitest without `chrome.*` (nothing below touches a browser API).
// ---------------------------------------------------------------------------

/** Ring-buffer capacity for console + network telemetry. */
export const TELEMETRY_CAP = 200;

export interface ConsoleRow {
  level: string;
  text: string;
  t: number;
}

export interface NetworkRow {
  method: string;
  url: string;
  status?: number;
  t: number;
  /** CDP requestId — internal merge key for responseReceived, never surfaced. */
  requestId?: string;
}

/** Push onto a ring buffer, dropping the oldest row past `cap`. */
export function pushCapped<T>(buf: T[], item: T, cap = TELEMETRY_CAP): void {
  buf.push(item);
  if (buf.length > cap) buf.shift();
}

const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' ? (v as Record<string, unknown>) : {};

/** Fold one CDP telemetry event into the ring buffers. Console lines, page
 * exceptions and browser Log entries land in `console`; network requests land
 * in `network`, with the response STATUS merged into the matching pending row
 * by requestId (one line per request, not two) — a response whose request has
 * already scrolled out of the ring gets its own row so it is never lost. */
export function recordTelemetryEvent(
  bufs: { console: ConsoleRow[]; network: NetworkRow[] },
  method: string,
  params: Record<string, unknown>,
  t: number,
): void {
  if (method === 'Runtime.consoleAPICalled') {
    const args = Array.isArray(params.args) ? params.args : [];
    const text = args
      .map((a: unknown) => stringifyRemoteObject(rec(a)))
      .join(' ')
      .slice(0, 2000);
    pushCapped(bufs.console, { level: String(params.type ?? 'log'), text, t });
  } else if (method === 'Runtime.exceptionThrown') {
    const d = rec(params.exceptionDetails);
    const text = String(rec(d.exception).description ?? d.text ?? 'uncaught exception').slice(0, 2000);
    pushCapped(bufs.console, { level: 'error', text, t });
  } else if (method === 'Log.entryAdded') {
    const e = rec(params.entry);
    pushCapped(bufs.console, {
      level: String(e.level ?? 'info'),
      text: String(e.text ?? '').slice(0, 2000),
      t,
    });
  } else if (method === 'Network.requestWillBeSent') {
    const r = rec(params.request);
    pushCapped(bufs.network, {
      method: String(r.method ?? 'GET'),
      url: String(r.url ?? '').slice(0, 500),
      t,
      requestId: typeof params.requestId === 'string' ? params.requestId : undefined,
    });
  } else if (method === 'Network.responseReceived') {
    const r = rec(params.response);
    const status = Number(r.status ?? 0);
    const requestId = typeof params.requestId === 'string' ? params.requestId : undefined;
    const pending = requestId
      ? bufs.network.findLast((n) => n.requestId === requestId && n.status === undefined)
      : undefined;
    if (pending) {
      pending.status = status;
    } else {
      pushCapped(bufs.network, {
        method: 'GET',
        url: String(r.url ?? '').slice(0, 500),
        status,
        t,
        requestId,
      });
    }
  }
}

/** Render a CDP RemoteObject as the string a console reader expects. Objects
 * arrive as a type descriptor, not a value, so `String(obj)` would print
 * "[object Object]" for exactly the logs worth reading. */
export function stringifyRemoteObject(arg: Record<string, unknown>): string {
  if ('value' in arg) {
    const v = arg.value;
    if (typeof v === 'string') return v;
    try {
      return JSON.stringify(v) ?? 'undefined';
    } catch {
      return String(v);
    }
  }
  if (typeof arg.description === 'string') return arg.description;
  if (arg.type === 'undefined') return 'undefined';
  if (arg.subtype === 'null') return 'null';
  return String(arg.className ?? arg.type ?? '');
}

/** Filter rows by a caller-supplied pattern, regex-safely: an invalid regex
 * (e.g. a bare "(") degrades to a case-insensitive SUBSTRING match instead of
 * throwing back at the remote caller or silently matching nothing. */
export function filterByPattern<T>(
  rows: readonly T[],
  pattern: string | undefined,
  textOf: (row: T) => string,
): T[] {
  if (!pattern) return [...rows];
  try {
    const re = new RegExp(pattern, 'i');
    return rows.filter((row) => re.test(textOf(row)));
  } catch {
    const needle = pattern.toLowerCase();
    return rows.filter((row) => textOf(row).toLowerCase().includes(needle));
  }
}

/** Clamp a telemetry read limit into [1, TELEMETRY_CAP]; default 40. */
export function clampLimit(limit?: number, cap = TELEMETRY_CAP): number {
  return Math.max(1, Math.min(limit ?? 40, cap));
}

/** CDP `modifiers` bitmask bits (Alt=1, Control=2, Meta=4, Shift=8), with the
 * aliases people actually type. */
export const MODIFIER_BITS: Record<string, number> = {
  alt: 1,
  option: 1,
  ctrl: 2,
  control: 2,
  meta: 4,
  cmd: 4,
  command: 4,
  win: 4,
  windows: 4,
  shift: 8,
};

/** {code, keyCode, shift} for a printable character, so trusted key events carry
 * a real physical-key identity. The `text` field drives the insertion, but
 * code/keyCode matter for editors that key off them (Google Docs) and for
 * auto-format triggers (the Space after "* "). A shifted char (A–Z and the
 * symbols below) reports its base key + the Shift modifier, exactly as a real
 * keyboard would. Null for chars with no physical key (emoji/CJK/accented) —
 * the caller falls back to `Input.insertText`. */
export function keyInfoForChar(ch: string): { code: string; keyCode: number; shift: boolean } | null {
  if (ch >= 'a' && ch <= 'z') return { code: `Key${ch.toUpperCase()}`, keyCode: ch.toUpperCase().charCodeAt(0), shift: false };
  if (ch >= 'A' && ch <= 'Z') return { code: `Key${ch}`, keyCode: ch.charCodeAt(0), shift: true };
  if (ch >= '0' && ch <= '9') return { code: `Digit${ch}`, keyCode: ch.charCodeAt(0), shift: false };
  return SYMBOL_KEYS[ch] ?? null;
}

/** ASCII punctuation + space → {code, keyCode, shift}. Shifted symbols share
 * the physical key (and keyCode) of their unshifted sibling and set shift. */
const SYMBOL_KEYS: Record<string, { code: string; keyCode: number; shift: boolean }> = {
  ' ': { code: 'Space', keyCode: 32, shift: false },
  ';': { code: 'Semicolon', keyCode: 186, shift: false },
  ':': { code: 'Semicolon', keyCode: 186, shift: true },
  '=': { code: 'Equal', keyCode: 187, shift: false },
  '+': { code: 'Equal', keyCode: 187, shift: true },
  ',': { code: 'Comma', keyCode: 188, shift: false },
  '<': { code: 'Comma', keyCode: 188, shift: true },
  '-': { code: 'Minus', keyCode: 189, shift: false },
  _: { code: 'Minus', keyCode: 189, shift: true },
  '.': { code: 'Period', keyCode: 190, shift: false },
  '>': { code: 'Period', keyCode: 190, shift: true },
  '/': { code: 'Slash', keyCode: 191, shift: false },
  '?': { code: 'Slash', keyCode: 191, shift: true },
  '`': { code: 'Backquote', keyCode: 192, shift: false },
  '~': { code: 'Backquote', keyCode: 192, shift: true },
  '[': { code: 'BracketLeft', keyCode: 219, shift: false },
  '{': { code: 'BracketLeft', keyCode: 219, shift: true },
  '\\': { code: 'Backslash', keyCode: 220, shift: false },
  '|': { code: 'Backslash', keyCode: 220, shift: true },
  ']': { code: 'BracketRight', keyCode: 221, shift: false },
  '}': { code: 'BracketRight', keyCode: 221, shift: true },
  "'": { code: 'Quote', keyCode: 222, shift: false },
  '"': { code: 'Quote', keyCode: 222, shift: true },
  '!': { code: 'Digit1', keyCode: 49, shift: true },
  '@': { code: 'Digit2', keyCode: 50, shift: true },
  '#': { code: 'Digit3', keyCode: 51, shift: true },
  $: { code: 'Digit4', keyCode: 52, shift: true },
  '%': { code: 'Digit5', keyCode: 53, shift: true },
  '^': { code: 'Digit6', keyCode: 54, shift: true },
  '&': { code: 'Digit7', keyCode: 55, shift: true },
  '*': { code: 'Digit8', keyCode: 56, shift: true },
  '(': { code: 'Digit9', keyCode: 57, shift: true },
  ')': { code: 'Digit0', keyCode: 48, shift: true },
};

/** macOS NSResponder editing selector for a chord (lowercased parts, main key
 * last), or undefined. Chrome only performs a Cmd/Option editing shortcut on
 * macOS when the matching command rides the keyDown — this is what makes cmd+a
 * select, and what makes caret/selection navigation (shift+arrow, jump by
 * word/line/document) take effect in editors. Order-independent across the
 * modifier parts. A wrong/absent name is simply ignored by Chrome, degrading to
 * the raw key event — never a regression. */
export function macCommandFor(parts: string[]): string | undefined {
  const mods = parts.slice(0, -1);
  const has = (...names: string[]): boolean => names.some((n) => mods.includes(n));
  const shift = has('shift');
  const meta = has('meta', 'cmd', 'command', 'win', 'windows');
  const alt = has('alt', 'option');
  const main = parts[parts.length - 1] ?? '';
  const ext = shift ? 'AndModifySelection' : '';

  // Letter shortcuts (Cmd only).
  if (meta && !alt) {
    if (!shift) {
      const m: Record<string, string> = { a: 'selectAll', c: 'copy', x: 'cut', v: 'paste', z: 'undo' };
      if (m[main]) return m[main];
    }
    if (shift && main === 'z') return 'redo';
  }

  // Arrow navigation / selection. cmd → line/document ends, alt → by word,
  // otherwise by char/line; shift extends the selection.
  const dir: Record<string, 'Left' | 'Right' | 'Up' | 'Down'> = {
    arrowleft: 'Left',
    arrowright: 'Right',
    arrowup: 'Up',
    arrowdown: 'Down',
  };
  const d = dir[main];
  if (d) {
    if (meta) {
      if (d === 'Left') return 'moveToLeftEndOfLine' + ext;
      if (d === 'Right') return 'moveToRightEndOfLine' + ext;
      if (d === 'Up') return 'moveToBeginningOfDocument' + ext;
      return 'moveToEndOfDocument' + ext; // Down
    }
    if (alt && (d === 'Left' || d === 'Right')) return 'moveWord' + d + ext;
    // char/line move; only meaningful to attach a command when extending.
    if (shift) return 'move' + d + 'AndModifySelection';
    return undefined;
  }

  // Line ends and deletion helpers.
  if (main === 'home') return (meta ? 'moveToBeginningOfDocument' : 'moveToBeginningOfLine') + ext;
  if (main === 'end') return (meta ? 'moveToEndOfDocument' : 'moveToEndOfLine') + ext;
  if (main === 'backspace') {
    if (alt) return 'deleteWordBackward';
    if (meta) return 'deleteToBeginningOfLine';
  }
  if ((main === 'delete' || main === 'forwarddelete') && alt) return 'deleteWordForward';

  return undefined;
}

/** A parsed chord, ready to dispatch. `single` set means "no modifiers held" —
 * the caller should use the plain text-producing key path instead. */
export interface ChordPlan {
  single?: string;
  modifiers: number;
  key: string;
  code: string;
  windowsVirtualKeyCode?: number;
  commands?: string[];
}

/** Parse "Meta+a" / "Control+Shift+p" / "Enter" into modifiers + key identity +
 * the macOS editing command (when one applies). Pure — unit-tested directly. */
export function planChord(chord: string): ChordPlan {
  const parts = chord
    .split('+')
    .map((k) => k.trim())
    .filter(Boolean);
  if (parts.length <= 1) {
    const single = parts[0] ?? chord;
    return { single, modifiers: 0, key: single, code: single };
  }
  let modifiers = 0;
  for (const m of parts.slice(0, -1)) modifiers |= MODIFIER_BITS[m.toLowerCase()] ?? 0;
  const main = parts[parts.length - 1]!;
  const defn = KEY_DEFS[main];
  const info = defn ? null : main.length === 1 ? keyInfoForChar(main) : null;
  const command = macCommandFor(parts.map((p) => p.toLowerCase()));
  const keyCode = (defn?.windowsVirtualKeyCode as number | undefined) ?? info?.keyCode;
  return {
    modifiers,
    key: (defn?.key as string | undefined) ?? main,
    code: (defn?.code as string | undefined) ?? info?.code ?? main,
    ...(keyCode ? { windowsVirtualKeyCode: keyCode } : {}),
    ...(command ? { commands: [command] } : {}),
  };
}
