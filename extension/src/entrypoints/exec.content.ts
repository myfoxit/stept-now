import { defineContentScript } from 'wxt/utils/define-content-script';
import {
  axName,
  buildTarget,
  findByText,
  INDEX_ATTR,
  indexInteractive,
  interactiveAncestor,
  isVisibleLenient,
  normText,
  OVERLAY_SELECTOR,
  pageText,
  serializeCompact,
  stampIndex,
  topmostOverlay,
  visibleText,
  type IndexedElement,
} from '@stept/dom-capture';
import { waitForPageSettled } from '../dom-settle';
import type { BgToExec, ExecOpName, ExecResult } from '../messages';

/** Remote drive's in-page island: the DOM half of the exec-op contract
 * (docs/MCP-CONTRACTS.md). The background owns the run loop and the trusted CDP
 * input (`driver/cdp.ts`); this script owns everything that needs a document —
 * indexing what is on the page, measuring where to click, reading values,
 * value-setting through native setters, settling.
 *
 * RPC envelope: `{type:'stept-exec', op, args}` → `{ok, result}` |
 * `{ok:false, error}`. `handleExecOp` is exported and chrome-free so every op
 * unit-tests in jsdom.
 */
export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: false,
  runAt: 'document_idle',
  main() {
    const g = globalThis as unknown as { __steptExecInit?: boolean };
    if (g.__steptExecInit) return;
    g.__steptExecInit = true;

    chrome.runtime.onMessage.addListener((msg: BgToExec, _sender, sendResponse) => {
      if (!msg || msg.type !== 'stept-exec') return;
      void handleExecOp(msg.op, msg.args ?? {})
        .then((result) => sendResponse({ ok: true, result } satisfies ExecResult))
        .catch((err: unknown) =>
          sendResponse({
            ok: false,
            error: err instanceof Error ? err.message : String(err),
          } satisfies ExecResult),
        );
      return true; // async response
    });
  },
});

/** Element cap for the compact DOM. Far above what one page of serialized text
 * shows: EVERY candidate gets an addressable [index] and the serialization
 * pages through them via `offset`, so elements past the char budget stay
 * actable. */
const INDEX_CAP = 2000;
/** Char budget per serialized listing page (server truncates at 14k). */
const ELEMENTS_BUDGET = 12_000;

/** The extension's own injected surfaces (drive ring/banner, guide overlay,
 * selector picker) plus the host-page opt-out fence the widget honours too —
 * the AI must never see, click, or read its own chrome. */
const EXCLUDE_SELECTOR = '[data-stept-drive],[data-stept-guide],[data-stept-picker],[data-stept-no-ai]';

const isExcluded = (el: Element): boolean => Boolean(el.closest(EXCLUDE_SELECTOR));

// ---------------------------------------------------------------------------
// stable element identity — an [index] lives as long as the page does
// ---------------------------------------------------------------------------
//
// `indexInteractive` numbers elements by their position in the current
// serialization order, so any page change RENUMBERED everything and an index
// from the immediately-preceding snapshot could point at the wrong element (or
// nothing). Instead, each element is assigned a stable id the first time it is
// seen and keeps it across snapshots; new elements get fresh ids, removed
// elements retire theirs. A full navigation reloads this script, which is
// exactly the "invalidate on navigation" boundary the contract wants.

interface StableDescriptor {
  tag: string;
  role: string;
  name: string;
  text: string;
}

let nextStableId = 0;
const stableIdOf = new WeakMap<Element, number>();
const stableRefs = new Map<number, WeakRef<Element>>();
/** What each id looked like at extraction — the heal fuel when a framework
 * re-render replaced the node (same control, new DOM identity). */
const stableDescriptors = new Map<number, StableDescriptor>();

/** Rewrite `entry.index` to stable ids (assigning fresh ones as needed) so the
 * serialization, the stamps and `find` all speak the same durable address. */
function assignStableIds(index: IndexedElement[]): void {
  for (const entry of index) {
    let id = stableIdOf.get(entry.el);
    if (id === undefined) {
      id = nextStableId++;
      stableIdOf.set(entry.el, id);
    }
    entry.index = id;
    stableRefs.set(id, new WeakRef(entry.el));
    stableDescriptors.set(id, {
      tag: entry.tag,
      role: entry.role,
      name: entry.name,
      text: entry.text,
    });
  }
  if (stableRefs.size > 4 * INDEX_CAP) pruneStableRefs();
}

function pruneStableRefs(): void {
  for (const [id, ref] of stableRefs) {
    const el = ref.deref();
    if (!el || !el.isConnected) {
      stableRefs.delete(id);
      stableDescriptors.delete(id);
    }
  }
}

/** Test seam: back to a blank registry (a fresh page load, in effect). */
export function resetStableIndexForTests(): void {
  nextStableId = 0;
  stableRefs.clear();
  stableDescriptors.clear();
}

/** The node a framework re-render put where the indexed one used to be: same
 * tag and same accessible name / visible text, and UNAMBIGUOUS (exactly one
 * candidate) — guessing between twins would click the wrong control. */
function healIndex(id: number): Element | null {
  const desc = stableDescriptors.get(id);
  const needle = desc && (desc.name || desc.text);
  if (!desc || !needle) return null;
  const wanted = normText(needle, 120).toLowerCase();
  const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
  const matches = index.filter((entry) => {
    if (entry.tag !== desc.tag) return false;
    const name = normText(entry.name, 120).toLowerCase();
    const text = normText(entry.text, 120).toLowerCase();
    return name === wanted || text === wanted;
  });
  if (matches.length !== 1) return null;
  const el = matches[0]!.el;
  // Restore identity: a node that never had an id inherits this one; a node
  // already known under another id is merely aliased (both addresses work).
  if (!stableIdOf.has(el)) stableIdOf.set(el, id);
  stableRefs.set(id, new WeakRef(el));
  el.setAttribute(INDEX_ATTR, String(id));
  return el;
}

/** Execute one exec-op against the live document. Throws on failure — the
 * listener maps a throw to `{ok:false, error}`. */
export async function handleExecOp(op: ExecOpName, args: Record<string, unknown>): Promise<unknown> {
  switch (op) {
    case 'url':
      return { url: location.href, title: document.title };

    case 'compact-dom': {
      const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
      assignStableIds(index);
      stampIndex(document, index);
      const offset = Math.max(0, Math.trunc(Number(args.offset ?? 0)) || 0);
      return { text: serializeCompact(index, ELEMENTS_BUDGET, offset), count: index.length };
    }

    case 'resolve-index': {
      const el = byIndex(args);
      if (!el) return { found: false };
      return measure(el);
    }

    case 'resolve-semantic': {
      // Find-by-accessible-name at act time: index staleness cannot bite when
      // the target is named the way the user (and the model) sees it.
      const name = normText(String(args.name ?? ''), 120);
      if (!name) throw new Error('semantic targeting needs a name (the visible label)');
      const role = String(args.role ?? '')
        .trim()
        .toLowerCase();
      const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
      assignStableIds(index);
      const pool = role
        ? index.filter((entry) => entry.role === role || entry.tag === role)
        : index;
      const hit = findByText(pool, name, 1)[0];
      const el = hit ? pool.find((entry) => entry.index === hit.index)?.el : undefined;
      if (!el) {
        throw new Error(
          role
            ? `no ${role} matching "${name}" on this page — take a fresh snapshot or try browser_find`
            : `nothing matching "${name}" on this page — take a fresh snapshot or try browser_find`,
        );
      }
      return { ...measure(el), index: hit!.index };
    }

    case 'describe':
      return buildTarget(requireIndex(args));

    case 'prepare': {
      const el = requireIndex(args);
      try {
        el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' as ScrollBehavior });
      } catch {
        // detached mid-flight — measure() reports where it is (or is not)
      }
      await nextFrame(); // give layout a frame so the point is where it will BE
      return measure(el);
    }

    case 'set-value': {
      const el = requireIndex(args);
      refusePassword(el);
      setNativeValue(el, String(args.value ?? args.text ?? ''));
      return true;
    }

    case 'select-all': {
      // Focus the field and select its whole current value so the next typed
      // character REPLACES it instead of inserting at the caret mid-text.
      const el = requireIndex(args);
      if (el instanceof HTMLElement) el.focus({ preventScroll: true });
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
        el.select();
      } else if (el instanceof HTMLElement && el.isContentEditable) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      return true;
    }

    case 'select': {
      const el = requireIndex(args);
      if (!(el instanceof HTMLSelectElement)) throw new Error('element is not a <select>');
      const wanted = firstString(args.label, args.value, args.text);
      if (wanted === undefined) throw new Error('select needs a value or label');
      const option =
        [...el.options].find((o) => o.value === wanted) ??
        [...el.options].find((o) => o.text.trim() === wanted.trim());
      if (!option) throw new Error(`no option matching "${wanted}"`);
      el.value = option.value;
      el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
      return true;
    }

    case 'set-checked': {
      const el = requireIndex(args);
      if (!(el instanceof HTMLInputElement) || (el.type !== 'checkbox' && el.type !== 'radio')) {
        throw new Error('element is not a checkbox/radio');
      }
      if (el.checked !== Boolean(args.checked)) el.click(); // click keeps frameworks in sync
      return true;
    }

    case 'extract': {
      const kind = String(args.kind ?? args.extractKind ?? 'text');
      if (kind === 'url') return { kind, value: location.href };
      const el = requireIndex(args);
      refusePassword(el);
      if (kind === 'attr') {
        const attr = String(args.attr ?? '');
        if (!attr) throw new Error('extract kind=attr needs an attr name');
        return { kind, value: el.getAttribute(attr) ?? '' };
      }
      // Form fields hold their content in .value, not text — a revealed OTP or
      // a filled input lives there; textContent would return "".
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
        return { kind: 'text', value: (el.value ?? '').trim() };
      }
      return { kind: 'text', value: ((el as HTMLElement).innerText ?? el.textContent ?? '').trim() };
    }

    case 'find': {
      const query = String(args.query ?? '').trim();
      if (!query) return [];
      const limit = clampInt(args.limit, 1, 50, 10);
      // A fresh index (same exclusions as compact-dom) carrying the STABLE ids,
      // so every hit's index is the durable address `resolve-index`/act uses.
      const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
      assignStableIds(index);
      return findByText(index, query, limit).map((f) => ({
        index: f.index,
        text: f.text || f.name,
        tag: f.tag,
        visible: f.visible,
      }));
    }

    case 'page-text':
      return pageText(document, clampInt(args.maxChars, 200, 50_000, 8000), isExcluded);

    case 'hit-test': {
      const x = Number(args.x);
      const y = Number(args.y);
      const el = Number.isFinite(x) && Number.isFinite(y) ? elementFromPointSafe(x, y) : null;
      if (!el) return null;
      // Key the description off the nearest interactive ancestor — a click can
      // land on an inner glyph of a real control.
      const control = interactiveAncestor(el);
      const idxAttr = control.getAttribute(INDEX_ATTR) ?? el.getAttribute(INDEX_ATTR);
      const text = normText(axName(control) || visibleText(control, 80), 80);
      return {
        tag: el.tagName.toLowerCase(),
        text,
        ...(idxAttr != null && idxAttr !== '' ? { idx: Number(idxAttr) } : {}),
      };
    }

    case 'overlay-open': {
      const containers = [...document.querySelectorAll(OVERLAY_SELECTOR)].filter(
        (el) => !isExcluded(el) && isVisibleLenient(el) && el.getClientRects().length > 0,
      );
      if (containers.length === 0) return { open: false };
      const candidates = containers.map((el, order) => ({
        el,
        overlay: { key: String(order), z: zIndexOf(el), order },
      }));
      const top = topmostOverlay(candidates);
      const winner = candidates.find((c) => c.overlay.key === top?.key)?.el ?? containers[0]!;
      const role =
        winner.getAttribute('role') ?? (winner.getAttribute('aria-modal') === 'true' ? 'modal' : 'overlay');
      const name = normText(axName(winner) || visibleText(winner, 60), 60);
      return { open: true, description: name ? `${role} "${name}"` : role };
    }

    case 'scroll-at': {
      // Scroll the scrollable ANCESTOR under (x,y), not blindly the window —
      // app shells and editors scroll a nested pane, where a fixed-point CDP
      // wheel silently no-ops on a backgrounded tab. Reports whether anything
      // moved so the caller can fall back to a trusted wheel event.
      const dx = Number(args.dx ?? 0) || 0;
      const dy = Number(args.dy ?? 0) || 0;
      const px = Number(args.x);
      const py = Number(args.y);
      const x = Number.isFinite(px) ? px : Math.floor(window.innerWidth / 2);
      const y = Number.isFinite(py) ? py : Math.floor(window.innerHeight / 2);
      const scroller = findScrollable(elementFromPointSafe(x, y), dx, dy);
      const beforeTop = scroller.scrollTop;
      const beforeLeft = scroller.scrollLeft;
      try {
        scroller.scrollBy({ left: dx, top: dy, behavior: 'instant' as ScrollBehavior });
      } catch {
        scroller.scrollTop = beforeTop + dy;
        scroller.scrollLeft = beforeLeft + dx;
      }
      await sleep(40);
      const moved =
        Math.abs(scroller.scrollTop - beforeTop) > 2 || Math.abs(scroller.scrollLeft - beforeLeft) > 2;
      return { moved };
    }

    case 'dom-settle': {
      // Full hydration gate before a snapshot serialize: readyState (initial
      // parse) + one rAF (first paint) + a MutationObserver quiet-window (SPA
      // renders that land after 'complete'). quietMs is capped by the hard
      // ceiling so a silly arg can't out-wait maxMs.
      const requested = Number(args.ms);
      const quietMs = Math.min(Number.isFinite(requested) && requested > 0 ? requested : 400, 3000);
      await waitForPageSettled({ quietMs, maxMs: 3000 });
      return true;
    }

    case 'wait-for': {
      // Wait for a selector / visible text / plain settle, bounded. The result
      // says whether the condition was met — the caller reports a timeout
      // honestly instead of snapshotting and hoping.
      const timeoutMs = clampInt(args.timeoutMs, 100, 20_000, 5000);
      const selector = firstString(args.selector)?.trim() || null;
      const text = selector ? null : firstString(args.text, args.query)?.trim() || null;
      const startedAt = Date.now();
      if (!selector && !text) {
        await waitForPageSettled({ maxMs: timeoutMs });
        return { met: true, waited_ms: Date.now() - startedAt };
      }
      const needle = text ? normText(text, 200).toLowerCase() : '';
      const check = (): boolean => {
        if (selector) {
          try {
            return [...document.querySelectorAll(selector)].some(
              (el) => !isExcluded(el) && isVisibleLenient(el),
            );
          } catch {
            throw new Error(`"${selector}" is not a valid CSS selector`);
          }
        }
        return pageText(document, 60_000, isExcluded).toLowerCase().includes(needle);
      };
      for (;;) {
        if (check()) return { met: true, waited_ms: Date.now() - startedAt };
        if (Date.now() - startedAt >= timeoutMs) {
          return { met: false, waited_ms: Date.now() - startedAt };
        }
        await sleep(120);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// helpers (exported where a test exercises them directly)
// ---------------------------------------------------------------------------

/** The element behind a stable index: the live registry reference first, the
 * stamped attribute second (covers a worker that missed the registry write),
 * and a descriptor-based heal last — a framework re-render that replaced the
 * node no longer voids an index taken one snapshot ago. */
function byIndex(args: Record<string, unknown>): Element | null {
  const idx = Number(args.index);
  if (!Number.isFinite(idx)) return null;
  const id = Math.trunc(idx);
  const held = stableRefs.get(id)?.deref();
  if (held?.isConnected) return held;
  const stamped = document.querySelector(`[${INDEX_ATTR}="${id}"]`);
  if (stamped) return stamped;
  return healIndex(id);
}

function requireIndex(args: Record<string, unknown>): Element {
  const el = byIndex(args);
  if (!el) {
    throw new Error(`no element at index ${String(args.index)} — take a fresh snapshot and pick again`);
  }
  return el;
}

/** Viewport CSS centre + advisory occlusion, mirroring the driver island's
 * measure(): occluded means something unrelated is painted over the click
 * point (a spinner, a menu animating open) — the caller settles + retries once
 * rather than clicking through it. */
function measure(el: Element): {
  found: true;
  x: number;
  y: number;
  tag: string;
  occluded: boolean;
  contentEditable: boolean;
  editable: boolean;
  isPassword: boolean;
} {
  const rect = el.getBoundingClientRect();
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  const hit = elementFromPointSafe(x, y);
  const occluded = !!hit && hit !== el && !el.contains(hit) && !hit.contains(el);
  const contentEditable = Boolean(el instanceof HTMLElement && el.isContentEditable);
  const editable =
    contentEditable ||
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement;
  // Reported so the drive controller can refuse to TYPE here too: its typing
  // path goes through CDP (trusted keystrokes at a coordinate), which never
  // touches this island's `set-value` guard.
  const isPassword = el instanceof HTMLInputElement && el.type === 'password';
  return {
    found: true,
    x,
    y,
    tag: el.tagName.toLowerCase(),
    occluded,
    contentEditable,
    editable,
    isPassword,
  };
}

/** Never read from, never write into a password field — the hard line the
 * remote-drive contract draws (same wording the MCP tools surface). */
function refusePassword(el: Element): void {
  if (el instanceof HTMLInputElement && el.type === 'password') {
    throw new Error('refusing to touch a password field');
  }
}

/** Set a field's value through the NATIVE prototype setter + real input/change
 * events. React (and Vue) install an instance-level value interceptor to track
 * edits, and silently revert a naive `el.value =` on the next render — going
 * through the prototype setter is what makes the framework accept the value as
 * user input. */
function setNativeValue(el: Element, value: string): void {
  const view = el.ownerDocument.defaultView ?? window;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    const proto =
      el instanceof HTMLInputElement ? view.HTMLInputElement.prototype : view.HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    el.focus({ preventScroll: true });
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  } else if (el instanceof HTMLSelectElement) {
    const setter = Object.getOwnPropertyDescriptor(view.HTMLSelectElement.prototype, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  } else if (el instanceof HTMLElement && el.isContentEditable) {
    el.focus({ preventScroll: true });
    // innerText (layout-aware: \n → <br>) where the runtime implements it —
    // jsdom does not, and an own-prop write there would leave the DOM untouched.
    if ('innerText' in view.HTMLElement.prototype) el.innerText = value;
    else el.textContent = value;
    el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true }));
  } else {
    throw new Error('element is not editable');
  }
}

/** Nearest scrollable ancestor of `start` that overflows in the requested
 * axis, else the document scrolling root. */
function findScrollable(start: Element | null, dx: number, dy: number): Element {
  let el: Element | null = start;
  while (el && el !== document.body && el !== document.documentElement) {
    const s = getComputedStyle(el);
    const canY = dy !== 0 && /(auto|scroll|overlay)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1;
    const canX = dx !== 0 && /(auto|scroll|overlay)/.test(s.overflowX) && el.scrollWidth > el.clientWidth + 1;
    if (canY || canX) return el;
    el = el.parentElement;
  }
  return document.scrollingElement ?? document.documentElement;
}

function zIndexOf(el: Element): number {
  const v = el.ownerDocument.defaultView?.getComputedStyle(el).zIndex;
  const n = v ? Number.parseInt(v, 10) : NaN;
  return Number.isFinite(n) ? n : 0;
}

/** `document.elementFromPoint`, tolerated in environments that lack layout. */
function elementFromPointSafe(x: number, y: number): Element | null {
  try {
    return document.elementFromPoint(x, y);
  } catch {
    return null;
  }
}

function firstString(...values: unknown[]): string | undefined {
  for (const v of values) if (v != null) return String(v);
  return undefined;
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = Math.trunc(Number(value));
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.max(min, Math.min(n, max));
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function nextFrame(): Promise<void> {
  return new Promise((r) =>
    typeof requestAnimationFrame === 'function' ? requestAnimationFrame(() => r()) : setTimeout(r, 16),
  );
}
