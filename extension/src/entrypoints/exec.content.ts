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
} from '@stept/dom-capture';
import { waitForDomSettle } from '../dom-settle';
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

/** Execute one exec-op against the live document. Throws on failure — the
 * listener maps a throw to `{ok:false, error}`. */
export async function handleExecOp(op: ExecOpName, args: Record<string, unknown>): Promise<unknown> {
  switch (op) {
    case 'url':
      return { url: location.href, title: document.title };

    case 'compact-dom': {
      const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
      stampIndex(document, index);
      const offset = Math.max(0, Math.trunc(Number(args.offset ?? 0)) || 0);
      return { text: serializeCompact(index, ELEMENTS_BUDGET, offset), count: index.length };
    }

    case 'resolve-index': {
      const el = byIndex(args);
      if (!el) return { found: false };
      return measure(el);
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
      // A fresh index (same exclusions as compact-dom), but report each hit
      // under its STAMPED idx when the last snapshot numbered it — so the
      // returned index is the one `resolve-index`/act can actually address.
      const index = indexInteractive(document, { cap: INDEX_CAP, exclude: isExcluded });
      return findByText(index, query, limit).map((f) => {
        const el = index[f.index]?.el;
        const stamped = el?.getAttribute(INDEX_ATTR);
        return {
          index: stamped != null && stamped !== '' ? Number(stamped) : f.index,
          text: f.text || f.name,
          tag: f.tag,
          visible: f.visible,
        };
      });
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
      // MutationObserver quiet-window before a snapshot serialize — readyState
      // settling can't see SPA renders that land after 'complete'. quietMs is
      // capped by the hard ceiling so a silly arg can't out-wait maxMs.
      const requested = Number(args.ms);
      const quietMs = Math.min(Number.isFinite(requested) && requested > 0 ? requested : 400, 3000);
      await waitForDomSettle({ quietMs, maxMs: 3000 });
      return true;
    }
  }
}

// ---------------------------------------------------------------------------
// helpers (exported where a test exercises them directly)
// ---------------------------------------------------------------------------

/** The element the last `compact-dom` stamped with this index, or null. */
function byIndex(args: Record<string, unknown>): Element | null {
  const idx = Number(args.index);
  if (!Number.isFinite(idx)) return null;
  return document.querySelector(`[${INDEX_ATTR}="${Math.trunc(idx)}"]`);
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
