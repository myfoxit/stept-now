import type { BgToExec, ExecOpName, ExecResult } from '../messages';
import type { DriveOp, DriveSnapshot, RemoteDriveState } from '../types';
import { CdpSession } from '../driver/cdp';
import { screenshotToCss } from './coord';

/**
 * One remotely-driven tab: the extension half of the MCP browser tools
 * (docs/MCP-CONTRACTS.md drive-op vocabulary). Ported from the old repo's
 * `drive-controller.ts`, re-cut onto stept-now's split: DOM reads run in the
 * exec island (`exec.content.ts`, EXT-1), trusted input + telemetry run over
 * the CDP session (`driver/cdp.ts`, EXT-1's remote surface).
 *
 * Every op resolves to a DriveSnapshot-ish payload so the MCP caller always
 * sees the page it just changed. Ops are strictly serialized — the gateway
 * awaits one ctrl at a time, but a retried/timed-out op must never interleave
 * with its successor on the same tab.
 */

const NO_TAB_ERROR = 'no driven tab — call browser_open first';
const NO_CHANGE_NOTE =
  'the click produced no visible change — the element may not be interactive, or the effect is outside the viewport';

/** The slice of CdpSession the controller drives — structural, so tests can
 * hand in a stub without a chrome.debugger. */
export type RemoteCdp = Pick<
  CdpSession,
  | 'click'
  | 'hover'
  | 'insertText'
  | 'typeText'
  | 'pressKey'
  | 'pressChord'
  | 'clearField'
  | 'scrollBy'
  | 'drag'
  | 'navigate'
  | 'awaitIdle'
  | 'evaluate'
  | 'screenshot'
  | 'viewportSize'
  | 'consoleMessages'
  | 'networkRequests'
  | 'detach'
>;

/** Browser/IO seam. The default implementation talks to chrome.* and the exec
 * island; tests inject fakes and exercise the op logic pure. */
export interface DriveDeps {
  attach(tabId: number): Promise<RemoteCdp>;
  /** RPC into the exec island; resolves the op's `result`, throws its error. */
  exec(tabId: number, op: ExecOpName, args?: Record<string, unknown>): Promise<unknown>;
  createTab(url: string): Promise<{ id?: number }>;
  tabExists(tabId: number): Promise<boolean>;
  closeTab(tabId: number): Promise<void>;
  resizeWindow(tabId: number, width: number, height: number): Promise<void>;
  onTabCreated(listener: (tab: chrome.tabs.Tab) => void): void;
  offTabCreated(listener: (tab: chrome.tabs.Tab) => void): void;
  windowType(windowId: number): Promise<string | null>;
  sleep(ms: number): Promise<void>;
}

/** Send one op into the exec island, injecting the script first when the page
 * pre-dates the extension (same defensive pattern as the driver island). */
async function execInTab(
  tabId: number,
  op: ExecOpName,
  args?: Record<string, unknown>,
): Promise<unknown> {
  const msg: BgToExec = { type: 'stept-exec', op, args };
  let res: ExecResult | undefined;
  try {
    res = (await chrome.tabs.sendMessage(tabId, msg)) as ExecResult | undefined;
  } catch {
    await chrome.scripting
      .executeScript({ target: { tabId }, files: ['content-scripts/exec.js'] })
      .catch(() => {});
    res = (await chrome.tabs.sendMessage(tabId, msg)) as ExecResult | undefined;
  }
  if (!res) throw new Error(`the page did not answer "${op}" — take a fresh snapshot`);
  if (!res.ok) throw new Error(res.error ?? `"${op}" failed in the page`);
  return res.result;
}

function defaultDeps(): DriveDeps {
  return {
    // A refusal THROWS (attachOrThrow): the MCP caller must get the real error,
    // never a silent downgrade to synthetic input.
    attach: async (tabId) => CdpSession.attachOrThrow(tabId),
    exec: execInTab,
    createTab: async (url) => chrome.tabs.create({ url, active: true }),
    tabExists: async (tabId) =>
      (await chrome.tabs
        .get(tabId)
        .then(() => true)
        .catch(() => false)) as boolean,
    closeTab: async (tabId) => chrome.tabs.remove(tabId).catch(() => {}),
    resizeWindow: async (tabId, width, height) => {
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (tab?.windowId != null) {
        await chrome.windows.update(tab.windowId, { width, height }).catch(() => {});
      }
    },
    onTabCreated: (listener) => chrome.tabs.onCreated.addListener(listener),
    offTabCreated: (listener) => chrome.tabs.onCreated.removeListener(listener),
    windowType: async (windowId) =>
      chrome.windows
        .get(windowId)
        .then((w) => w.type ?? null)
        .catch(() => null),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  };
}

interface ResolvedPoint {
  x: number;
  y: number;
  contentEditable: boolean;
  /** typing into it is refused — see `actType` */
  isPassword: boolean;
}

export class RemoteDriveController {
  private readonly deps: DriveDeps;
  private cdp: RemoteCdp | null = null;
  private tabId: number | null = null;
  /** The tab open() created — the only one close() is allowed to remove. */
  private createdTabId: number | null = null;
  /** Opener chain for popups the driven page opened (OAuth consent, payment).
   * A STACK so a popup-from-popup returns LIFO through the chain. */
  private popupOpenerTabIds: number[] = [];
  private pendingAdoptTabId: number | null = null;
  /** Previous offset-0 snapshot — baseline for the "no visible change" check.
   * Offset pages are partial views and never overwrite it. */
  private lastElements = '';
  private lastUrl = '';
  /** SCREENSHOT px → CSS px map, recomputed each snapshot (retina/zoom-safe). */
  private coord = { sw: 0, sh: 0, vw: 0, vh: 0 };
  /** Op mutex: the tail of the chain; every handle() appends to it. */
  private queue: Promise<unknown> = Promise.resolve();
  private opCount = 0;
  private startedAt = 0;
  /** Driven tab of a PREVIOUS session (journaled across worker restarts) —
   * the next 'open' adopts it instead of opening yet another tab. One-shot. */
  private adoptTabId: number | null;

  constructor(deps: DriveDeps = defaultDeps(), adoptTabId: number | null = null) {
    this.deps = deps;
    this.adoptTabId = adoptTabId;
  }

  /** What the side panel shows while an MCP client is driving. */
  get sessionState(): RemoteDriveState | null {
    if (this.tabId == null) return null;
    return {
      tabId: this.tabId,
      url: this.lastUrl,
      opCount: this.opCount,
      startedAt: this.startedAt,
    };
  }

  /** Serialized entry point: one op at a time, in arrival order, a failure
   * never wedges the chain. */
  handle(op: DriveOp): Promise<DriveSnapshot> {
    const run = this.queue.then(() => this.dispatch(op));
    this.queue = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  private async dispatch(msg: DriveOp): Promise<DriveSnapshot> {
    const a = msg.args ?? {};
    this.opCount += 1;
    switch (msg.op) {
      case 'open': {
        const wantShot = a.screenshot === true;
        // Reattach first: a live driven tab (this controller's, or the one a
        // previous worker journaled) is REUSED — recovery after a drop must
        // not stack tab after tab in the user's browser.
        const reattached = await this.reattachExisting(a.url);
        if (reattached != null) return this.snapshot(reattached, undefined, wantShot);
        await this.dispose();
        const tab = await this.deps.createTab(a.url ?? 'about:blank');
        this.tabId = tab.id ?? null;
        if (this.tabId == null) throw new Error('could not open a tab');
        this.createdTabId = this.tabId;
        this.startedAt = Date.now();
        this.opCount = 1;
        this.watchForPopups();
        this.cdp = await this.deps.attach(this.tabId);
        await this.settleIdle(4000, 400);
        return this.snapshot('opened a new tab in your browser', undefined, wantShot);
      }
      case 'navigate': {
        this.ensure();
        const note = (await this.adoptPendingPopup()) ?? (await this.revertIfPopupClosed());
        await this.cdp!.navigate(a.url ?? 'about:blank');
        await this.settleIdle(4000, 300);
        return this.snapshot(note ?? undefined);
      }
      case 'snapshot': {
        this.ensure();
        // Popups don't only open from acts — a page can window.open on a timer;
        // adopt here too so a bare snapshot lands on the surface the user sees.
        const note = (await this.adoptPendingPopup()) ?? (await this.revertIfPopupClosed());
        return this.snapshot(note ?? undefined, a.offset, a.screenshot === true);
      }
      case 'act': {
        this.ensure();
        const before = { elements: this.lastElements, url: this.lastUrl };
        await this.act(a);
        const kind = a.kind ?? 'click';
        const heavy = !!a.submit || kind === 'click';
        await this.settleIdle(heavy ? 3000 : 2000, heavy ? 450 : 250);
        const note = (await this.adoptPendingPopup()) ?? (await this.revertIfPopupClosed());
        const snap = await this.snapshot(note ?? undefined, undefined, a.screenshot === true);
        if (
          kind === 'click' &&
          !note &&
          before.elements &&
          snap.url === before.url &&
          snap.elements === before.elements
        ) {
          appendNote(snap, NO_CHANGE_NOTE);
        }
        return snap;
      }
      case 'scroll': {
        this.ensure();
        const amount = a.amount ?? 600;
        const dy = a.dir === 'up' ? -amount : amount;
        // The island scrolls the scrollable ANCESTOR under the point (inner
        // panes) and reports whether anything moved; when it didn't — or the
        // island can't answer — fall back to a trusted CDP wheel.
        const at = a.x != null && a.y != null ? this.mapScreenshotPoint(a.x, a.y) : null;
        const moved = await this.exec('scroll-at', { x: at?.x, y: at?.y, dx: 0, dy })
          .then((r) => (r as { moved?: boolean } | null)?.moved === true)
          .catch(() => false);
        if (!moved) await this.cdp!.scrollBy(0, dy, at?.x, at?.y);
        await this.deps.sleep(300);
        return this.snapshot();
      }
      case 'key': {
        this.ensure();
        const note = (await this.adoptPendingPopup()) ?? (await this.revertIfPopupClosed());
        const key = a.key ?? 'Enter';
        // A focused-modal Enter can silently do nothing — verify the key
        // actually dismissed the overlay, and say so when it did not.
        const checkOverlay = /^(enter|escape)$/i.test(key);
        const overlayBefore = checkOverlay ? await this.overlayOpen() : false;
        if (key.includes('+') && key.length > 1) await this.cdp!.pressChord(key);
        else await this.cdp!.pressKey(key);
        await this.settleIdle(2500, 300);
        const snap = await this.snapshot(note ?? undefined);
        if (overlayBefore && (await this.overlayOpen())) {
          appendNote(
            snap,
            `the overlay is still open — ${key} had no effect; its buttons are listed with [index] above, click one directly`,
          );
        }
        return snap;
      }
      case 'wait': {
        this.ensure();
        await this.deps.sleep(Math.min(a.ms ?? 1000, 8000));
        return this.snapshot();
      }
      case 'wait-for': {
        this.ensure();
        const raw = (await this.exec('wait-for', {
          selector: a.selector,
          text: a.text,
          timeoutMs: a.timeoutMs,
        })) as { met?: boolean; waited_ms?: number } | null;
        const met = raw?.met === true;
        const waited = typeof raw?.waited_ms === 'number' ? Math.round(raw.waited_ms) : 0;
        const what = a.selector
          ? `selector "${a.selector}"`
          : a.text
            ? `text "${a.text}"`
            : 'the page to settle';
        return this.snapshot(
          met
            ? `waited ${waited}ms for ${what}`
            : `timed out after ${waited}ms waiting for ${what} — the snapshot shows the page as it is now`,
        );
      }
      case 'close': {
        await this.dispose();
        return { url: '', elements: '', count: 0, note: 'drive session closed' };
      }
      case 'page-text': {
        this.ensure();
        const raw = await this.exec('page-text', { maxChars: a.maxChars ?? 20_000 });
        const snap = await this.snapshot();
        snap.pageText = typeof raw === 'string' ? raw : String(raw ?? '');
        return snap;
      }
      case 'find': {
        this.ensure();
        const query = (a.query ?? a.text ?? '').trim();
        if (!query) throw new Error('find needs a query (text to look for)');
        const raw = await this.exec('find', { query, limit: a.limit ?? 10 });
        const found = narrowFound(raw);
        const snap = await this.snapshot(
          found.length
            ? `${found.length} element(s) matching "${query}" — act on one by its [index]`
            : `nothing matching "${query}" on this page`,
        );
        snap.found = found;
        return snap;
      }
      case 'console': {
        this.ensure();
        const snap = await this.snapshotLite();
        snap.console = this.cdp!.consoleMessages(a.pattern ?? a.query, a.limit);
        return snap;
      }
      case 'network': {
        this.ensure();
        const snap = await this.snapshotLite();
        snap.network = this.cdp!.networkRequests(a.pattern ?? a.query, a.limit);
        return snap;
      }
      case 'extract': {
        this.ensure();
        if (a.index == null) throw new Error('extract needs an element index (from snapshot)');
        const kind = a.extractKind ?? 'text';
        const raw = (await this.exec('extract', { index: a.index, kind, attr: a.attr })) as {
          kind?: unknown;
          value?: unknown;
        } | null;
        const snap = await this.snapshotLite();
        snap.extracted = {
          kind: typeof raw?.kind === 'string' ? raw.kind : kind,
          value: typeof raw?.value === 'string' ? raw.value : '',
        };
        return snap;
      }
      case 'back':
      case 'forward': {
        this.ensure();
        await this.cdp!.evaluate(`history.${msg.op}()`, false);
        await this.settleIdle(4000, 350);
        return this.snapshot(`went ${msg.op}`);
      }
      case 'resize': {
        this.ensure();
        const width = Math.round(a.width ?? 1280);
        const height = Math.round(a.height ?? 800);
        if (width < 200 || height < 200 || width > 4000 || height > 4000) {
          throw new Error('resize needs width/height between 200 and 4000 CSS pixels');
        }
        // Resize the real window, not a CDP metrics override — an override
        // desynchronises the coordinate map every coordinate act depends on.
        await this.deps.resizeWindow(this.tabId!, width, height);
        await this.deps.sleep(400);
        return this.snapshot(`window resized to ${width}x${height}`);
      }
    }
  }

  /** Reuse a still-alive driven tab for 'open' instead of creating another:
   * this controller's own tab first, then the previous session's journaled tab
   * (worker/backend restarts drop the session but rarely the tab). Navigates
   * only when the caller asked for a DIFFERENT page than the tab is on.
   * Returns the caller-facing note, or null when there is nothing to reattach. */
  private async reattachExisting(url: string | undefined): Promise<string | null> {
    let target =
      this.tabId != null && (await this.deps.tabExists(this.tabId)) ? this.tabId : null;
    let adopted = false;
    if (target == null && this.adoptTabId != null) {
      target = (await this.deps.tabExists(this.adoptTabId)) ? this.adoptTabId : null;
      adopted = target != null;
    }
    this.adoptTabId = null; // one-shot: a dead hint must not resurrect later
    if (target == null) return null;
    if (this.tabId !== target || this.cdp == null) {
      await this.cdp?.detach().catch(() => {});
      this.tabId = target;
      // The session created this tab originally — close() may clean it up.
      this.createdTabId = target;
      this.watchForPopups();
      this.cdp = await this.deps.attach(target);
      if (this.startedAt === 0) this.startedAt = Date.now();
    }
    let navigated = false;
    if (url && url !== 'about:blank') {
      const current = await this.currentUrl();
      if (current !== url) {
        await this.cdp!.navigate(url);
        navigated = true;
      }
    }
    await this.settleIdle(navigated ? 4000 : 2000, navigated ? 400 : 250);
    return adopted
      ? 'reattached to the driven tab from the previous session (no new tab opened)'
      : 'reused the existing driven tab (no new tab opened)';
  }

  // ---- acting -------------------------------------------------------------

  private async act(a: NonNullable<DriveOp['args']>): Promise<void> {
    const kind = a.kind ?? 'click';

    if (kind === 'type') return this.actType(a);

    if (kind === 'select' || kind === 'check' || kind === 'uncheck') {
      const index =
        a.index ?? (a.name ? (await this.resolveSemantic(a.role, a.name)).index : undefined);
      if (index == null) {
        throw new Error(`${kind} needs an element index (from snapshot) or a name`);
      }
      if (kind === 'select') await this.exec('select', { index, value: a.text ?? '' });
      else await this.exec('set-checked', { index, checked: kind === 'check' });
      return;
    }

    // Everything else needs a point: the element's (resolve-index / find by
    // accessible name), or the caller's screenshot-space x/y mapped through
    // the calibrated ratio.
    const p = await this.pointFor(a);
    switch (kind) {
      case 'click':
        await this.cdp!.click(p.x, p.y, 'left', 1);
        return;
      case 'double-click':
        await this.cdp!.click(p.x, p.y, 'left', 2);
        return;
      case 'right-click':
        await this.cdp!.click(p.x, p.y, 'right', 1);
        return;
      case 'hover':
        await this.cdp!.hover(p.x, p.y);
        return;
      case 'drag': {
        if (a.toX == null || a.toY == null) throw new Error('drag needs toX/toY (the drop point)');
        const to = this.mapScreenshotPoint(a.toX, a.toY);
        await this.cdp!.drag(p.x, p.y, to.x, to.y);
        return;
      }
    }
  }

  /** Trusted typing. Three targets, in preference order: an indexed element
   * (click to focus, clear, then insert), a coordinate (place the caret, then
   * type — canvas/rich editors, so NO clear: select-all there could wipe the
   * document), or whatever the page has focused (modal auto-focus flows). */
  private async actType(a: NonNullable<DriveOp['args']>): Promise<void> {
    const text = a.text ?? '';
    if (a.index != null || a.name) {
      const p =
        a.index != null
          ? await this.resolveIndex(a.index)
          : await this.resolveSemantic(a.role, a.name!);
      // The island refuses to read or set a password field's value; typing goes
      // through CDP instead, so the same line has to be drawn here or the
      // guarantee is only half true.
      if (p.isPassword) {
        throw new Error('refusing to type into a password field');
      }
      await this.cdp!.click(p.x, p.y, 'left', 1);
      await this.cdp!.clearField();
      if (p.contentEditable) await this.cdp!.typeText(text);
      else await this.cdp!.insertText(text);
    } else if (a.x != null && a.y != null) {
      const p = this.mapScreenshotPoint(a.x, a.y);
      await this.cdp!.click(p.x, p.y, 'left', 1);
      await this.cdp!.typeText(text);
    } else {
      await this.cdp!.typeText(text);
    }
    if (a.submit) await this.cdp!.pressKey('Enter');
  }

  /** Element point (preferred) or mapped coordinate point for an act. */
  private async pointFor(a: NonNullable<DriveOp['args']>): Promise<ResolvedPoint> {
    if (a.index != null) return this.resolveIndex(a.index);
    if (a.name) return this.resolveSemantic(a.role, a.name);
    if (a.x != null && a.y != null) {
      const p = this.mapScreenshotPoint(a.x, a.y);
      return { ...p, contentEditable: false, isPassword: false };
    }
    throw new Error(
      'act needs an element index (from snapshot), a name (accessible label), or x/y coordinates',
    );
  }

  /** SCREENSHOT px → CSS px via the per-snapshot calibrated ratio — correct
   * under devicePixelRatio and page zoom, unlike dividing by dpr. */
  private mapScreenshotPoint(imgX: number, imgY: number): { x: number; y: number } {
    const { x, y } = screenshotToCss(
      { imgX, imgY },
      {
        screenshotW: this.coord.sw,
        screenshotH: this.coord.sh,
        viewportW: this.coord.vw,
        viewportH: this.coord.vh,
      },
    );
    return { x, y };
  }

  /** Ask the island for the indexed element's click point (CSS px). */
  private async resolveIndex(index: number): Promise<ResolvedPoint> {
    const raw = (await this.exec('resolve-index', { index })) as {
      found?: boolean;
      hit?: boolean;
      x?: number;
      y?: number;
      contentEditable?: boolean;
      isPassword?: boolean;
    } | null;
    const found =
      !!raw &&
      raw.found !== false &&
      raw.hit !== false &&
      typeof raw.x === 'number' &&
      typeof raw.y === 'number';
    if (!found) {
      throw new Error(
        `no element at index ${index} — take a fresh snapshot (the page likely changed)`,
      );
    }
    return {
      x: raw.x as number,
      y: raw.y as number,
      contentEditable: raw.contentEditable === true,
      isPassword: raw.isPassword === true,
    };
  }

  /** Ask the island to find an element by accessible name (+ optional role) at
   * act time — the targeting path that survives index staleness entirely. */
  private async resolveSemantic(
    role: string | undefined,
    name: string,
  ): Promise<ResolvedPoint & { index?: number }> {
    const raw = (await this.exec('resolve-semantic', { role, name })) as {
      found?: boolean;
      x?: number;
      y?: number;
      index?: number;
      contentEditable?: boolean;
      isPassword?: boolean;
    } | null;
    if (!raw || raw.found !== true || typeof raw.x !== 'number' || typeof raw.y !== 'number') {
      throw new Error(
        `could not find "${name}" on this page — take a fresh snapshot or try browser_find`,
      );
    }
    return {
      x: raw.x,
      y: raw.y,
      contentEditable: raw.contentEditable === true,
      isPassword: raw.isPassword === true,
      ...(typeof raw.index === 'number' ? { index: raw.index } : {}),
    };
  }

  private async overlayOpen(): Promise<boolean> {
    const r = (await this.exec('overlay-open').catch(() => null)) as { open?: boolean } | null;
    return r?.open === true;
  }

  // ---- snapshots ----------------------------------------------------------

  private async snapshot(
    note?: string,
    offset?: number,
    wantShot = false,
  ): Promise<DriveSnapshot> {
    // Hydration gate first (readyState + rAF + mutation-quiet): readyState
    // alone can't see SPA renders that land after 'complete'.
    await this.exec('dom-settle', { ms: 200 }).catch(() => {});
    let [dom, url] = await Promise.all([this.extractDom(offset), this.currentUrl()]);
    if (!offset && (dom.count === 0 || !url)) {
      // An empty listing (or no URL) against a page that visibly renders is
      // the hydration race: the app painted after our quiet window, or the
      // exec island was injected mid-load. Settle harder once and re-extract
      // instead of handing the model a blank page.
      await this.exec('dom-settle', { ms: 600 }).catch(() => {});
      const [dom2, url2] = await Promise.all([this.extractDom(offset), this.currentUrl()]);
      if (dom2.count > 0) dom = dom2;
      if (url2) url = url2;
    }
    // Screenshot only on request — after settling, so the picture matches the
    // listing. The CDP capture is downscaled (≤1280 long edge) at the source.
    const [shot, vp] = wantShot
      ? await Promise.all([
          this.cdp!.screenshot().catch(() => null),
          this.cdp!.viewportSize().catch(() => ({ w: 0, h: 0 })),
        ])
      : [null, null];
    if (shot && vp && vp.w && vp.h) this.coord = { sw: shot.w, sh: shot.h, vw: vp.w, vh: vp.h };
    if (!offset) {
      this.lastElements = dom.text;
      this.lastUrl = url;
    }
    return {
      url,
      elements: dom.text,
      count: dom.count,
      screenshot: shot?.data,
      screenshotSize: shot ? { w: shot.w, h: shot.h } : undefined,
      note,
    };
  }

  private extractDom(offset?: number): Promise<{ text: string; count: number }> {
    return this.exec('compact-dom', { offset: offset ?? 0 })
      .then(narrowCompactDom)
      .catch(() => ({ text: '', count: 0 }));
  }

  /** URL only — for telemetry/extract reads where the caller wants the value,
   * not a fresh picture of the page. */
  private async snapshotLite(): Promise<DriveSnapshot> {
    return { url: await this.currentUrl(), elements: '', count: 0 };
  }

  /** The island's `url` op returns `{url, title}`. */
  private async currentUrl(): Promise<string> {
    const r = (await this.exec('url').catch(() => null)) as { url?: unknown } | null;
    return typeof r?.url === 'string' ? r.url : '';
  }

  // ---- popup following (LIFO through the opener chain) --------------------
  //
  // chrome sets `openerTabId` on a tab created by window.open in an EXISTING
  // window; a `window.open(url, name, 'width=…')` popup WINDOW (every real
  // OAuth consent screen) has no openerTabId at all — the second branch adopts
  // opener-less tabs that start a popup-type window while a session is live.
  // A tab the user opens themselves lands in a 'normal' window: never adopted.

  private onTabCreated = (tab: chrome.tabs.Tab): void => {
    if (this.tabId == null || tab.id == null) return;
    if (tab.openerTabId === this.tabId) {
      this.pendingAdoptTabId = tab.id;
      return;
    }
    if (tab.openerTabId != null || tab.windowId == null) return;
    const candidate = tab.id;
    void this.deps
      .windowType(tab.windowId)
      .then((type) => {
        if (type === 'popup' && this.tabId != null) this.pendingAdoptTabId = candidate;
      })
      .catch(() => {});
  };

  private watchForPopups(): void {
    this.deps.offTabCreated(this.onTabCreated);
    this.deps.onTabCreated(this.onTabCreated);
  }

  /** Move the session into a popup the driven page just opened. */
  private async adoptPendingPopup(): Promise<string | null> {
    const target = this.pendingAdoptTabId;
    this.pendingAdoptTabId = null;
    if (target == null || target === this.tabId) return null;
    if (!(await this.deps.tabExists(target))) return null; // opened and closed again

    await this.cdp?.detach().catch(() => {});
    if (this.tabId != null) this.popupOpenerTabIds.push(this.tabId);
    this.tabId = target;
    this.cdp = await this.deps.attach(target);
    // Consent screens redirect a few times before settling.
    await this.settleIdle(8000, 500);
    return 'this page opened a popup (sign-in or consent window) — now acting inside it; when it closes the session returns to the original page automatically';
  }

  /** Return to the opener once the popup is gone — LIFO through the chain,
   * skipping openers that closed in the meantime. */
  private async revertIfPopupClosed(): Promise<string | null> {
    if (this.popupOpenerTabIds.length === 0 || this.tabId == null) return null;
    if (await this.deps.tabExists(this.tabId)) return null;

    let opener: number | null = null;
    while (this.popupOpenerTabIds.length > 0) {
      const candidate = this.popupOpenerTabIds.pop()!;
      if (await this.deps.tabExists(candidate)) {
        opener = candidate;
        break;
      }
    }
    if (opener == null) return null; // whole chain gone — the next op reports the real error

    await this.cdp?.detach().catch(() => {});
    this.tabId = opener;
    this.cdp = await this.deps.attach(opener);
    await this.settleIdle(4000, 300);
    return 'the popup closed — back on the original page';
  }

  // ---- plumbing -----------------------------------------------------------

  private exec(op: ExecOpName, args?: Record<string, unknown>): Promise<unknown> {
    if (this.tabId == null) return Promise.reject(new Error(NO_TAB_ERROR));
    return this.deps.exec(this.tabId, op, args);
  }

  private ensure(): void {
    if (this.cdp == null || this.tabId == null) throw new Error(NO_TAB_ERROR);
  }

  /** Evidence-based settle: page idle (capped) + a short per-action beat. */
  private async settleIdle(idleCap: number, floorMs: number): Promise<void> {
    await this.cdp?.awaitIdle(idleCap).catch(() => {});
    await this.deps.sleep(floorMs);
  }

  async dispose(): Promise<void> {
    this.deps.offTabCreated(this.onTabCreated);
    await this.cdp?.detach().catch(() => {});
    this.cdp = null;
    // only ever close the tab WE created — an adopted popup is the page's own
    if (this.createdTabId != null) await this.deps.closeTab(this.createdTabId).catch(() => {});
    this.createdTabId = null;
    this.adoptTabId = null;
    this.tabId = null;
    this.popupOpenerTabIds = [];
    this.pendingAdoptTabId = null;
    this.lastElements = '';
    this.lastUrl = '';
    this.coord = { sw: 0, sh: 0, vw: 0, vh: 0 };
  }
}

/** Stack advisory notes without losing an earlier one (popup adoption + the
 * no-change advisory can both apply to the same act). */
function appendNote(snap: DriveSnapshot, text: string): void {
  snap.note = snap.note ? `${snap.note}\n${text}` : text;
}

function narrowCompactDom(raw: unknown): { text: string; count: number } {
  const r = raw as { text?: unknown; count?: unknown } | null;
  return {
    text: typeof r?.text === 'string' ? r.text : '',
    count: typeof r?.count === 'number' ? r.count : 0,
  };
}

function narrowFound(
  raw: unknown,
): Array<{ index: number; text: string; tag: string; visible: boolean }> {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((f): f is Record<string, unknown> => !!f && typeof f === 'object')
    .map((f) => ({
      index: typeof f['index'] === 'number' ? f['index'] : -1,
      text: typeof f['text'] === 'string' ? f['text'] : '',
      tag: typeof f['tag'] === 'string' ? f['tag'] : '',
      visible: f['visible'] !== false,
    }));
}
