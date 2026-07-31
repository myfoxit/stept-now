import { resolveStepTarget } from '../dom/resolve-step';
import { waitForDomSettle } from '../dom-settle';
import type { BgToDriver, DriverResolveResult } from '../messages';
import type { TourStep } from '../types';

/** Drive mode's in-page island: everything the background CANNOT do from a
 * service worker — resolve a step to a live element, measure where to click,
 * wait for the DOM to settle, and paint the "Stept is driving" affordances.
 *
 * The background owns the run loop and the trusted CDP input; this script owns
 * the DOM. Ported (subset) from the old repo's `executor.content.ts`: the
 * resolve/prepare/scroll/settle/hit-test ops, plus a SYNTHETIC act path used
 * only when `chrome.debugger` attach was refused.
 */
export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: false,
  runAt: 'document_idle',
  main() {
    const g = globalThis as unknown as { __steptDriverInit?: boolean };
    if (g.__steptDriverInit) return;
    g.__steptDriverInit = true;

    chrome.runtime.onMessage.addListener((msg: BgToDriver, _sender, sendResponse) => {
      if (!msg || typeof msg.type !== 'string' || !msg.type.startsWith('driver-')) return;
      void handle(msg)
        .then(sendResponse)
        .catch((err) => sendResponse({ found: false, detail: String(err) }));
      return true; // async response
    });
  },
});

async function handle(msg: BgToDriver): Promise<unknown> {
  switch (msg.type) {
    case 'driver-resolve':
      return measure(msg.step, false);
    case 'driver-prepare':
      return measure(msg.step, true);
    case 'driver-act':
      return syntheticAct(msg.step, msg.kind, msg.value);
    case 'driver-highlight':
      return highlight(msg.step, msg.ms);
    case 'driver-banner':
      return banner(msg.text, msg.ms);
    case 'driver-wait-element':
      return waitForElement(msg.step, msg.timeoutMs);
    case 'driver-settle':
      await waitForDomSettle({ quietMs: msg.quietMs, maxMs: msg.maxMs });
      return { found: true };
  }
}

function stepLike(step: TourStep) {
  return {
    target: step.target,
    selector: step.wait?.selector || step.selector,
    fallbackSelectors: step.fallback_selectors,
    textHint: step.text_hint,
  };
}

/** Resolve + measure. With `scroll`, the element is first brought into view and
 * given a frame to settle so the returned point is where it will BE, not where
 * it was mid-scroll. */
async function measure(step: TourStep, scroll: boolean): Promise<DriverResolveResult> {
  const r = resolveStepTarget(document, stepLike(step));
  const el = r.element as HTMLElement | null;
  if (!el) return { found: false, detail: r.detail, via: r.via };
  if (scroll) {
    try {
      el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' as ScrollBehavior });
    } catch {
      /* detached mid-flight */
    }
    await new Promise((res) => requestAnimationFrame(() => res(null)));
  }
  const rect = el.getBoundingClientRect();
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  // Hit-test the click point: an overlay (a spinner, a cookie banner, a menu
  // animating open) painted over the target means the click would land on the
  // WRONG element. The runner settles briefly and re-measures once.
  const hit = document.elementFromPoint(x, y);
  const occluded = !!hit && hit !== el && !el.contains(hit) && !hit.contains(el);
  return {
    found: true,
    x,
    y,
    healed: r.healed,
    via: r.via,
    confidence: r.confidence,
    detail: r.detail,
    contentEditable: el.isContentEditable,
    occluded,
  };
}

/** Fallback actuation when `chrome.debugger` attach was refused. Untrusted
 * events: `el.click()` and a React-aware value set. Good enough for most apps,
 * visibly fake to any that check `isTrusted`. */
async function syntheticAct(
  step: TourStep,
  kind: 'click' | 'fill',
  value?: string,
): Promise<DriverResolveResult> {
  const r = resolveStepTarget(document, stepLike(step));
  const el = r.element as HTMLElement | null;
  if (!el) return { found: false, detail: r.detail };
  try {
    el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' as ScrollBehavior });
  } catch {
    /* ignore */
  }
  if (kind === 'click') {
    el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, composed: true }));
    el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, composed: true }));
    el.click();
    return { found: true, healed: r.healed, via: r.via };
  }
  // fill: React/Vue track the value through the prototype setter, so assigning
  // `el.value` directly is silently reverted on the next render. Go through the
  // native setter, then fire input + change like a real keystroke burst would.
  const text = value ?? '';
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    el.focus();
    if (setter) setter.call(el, text);
    else el.value = text;
  } else if (el.isContentEditable) {
    el.focus();
    el.textContent = text;
  }
  el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  return { found: true, healed: r.healed, via: r.via };
}

/** Wait for a step's element to exist (MutationObserver + a poll fallback, so
 * a purely-CSS reveal still resolves). Used by `wait {for:"element"}` steps. */
function waitForElement(step: TourStep, timeoutMs: number): Promise<DriverResolveResult> {
  return new Promise((resolve) => {
    let done = false;
    const finish = (found: boolean) => {
      if (done) return;
      done = true;
      observer.disconnect();
      clearInterval(poll);
      clearTimeout(cap);
      resolve({ found });
    };
    const check = () => {
      if (resolveStepTarget(document, stepLike(step)).element) finish(true);
    };
    const observer = new MutationObserver(check);
    const poll = setInterval(check, 300);
    const cap = setTimeout(() => finish(false), Math.max(100, timeoutMs));
    try {
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
    } catch {
      /* poll-only */
    }
    check();
  });
}

// ---------------------------------------------------------------------------
// drive affordances: a ring on the element being acted on + a status banner
// ---------------------------------------------------------------------------

let overlayRoot: ShadowRoot | null = null;

function ensureOverlay(): ShadowRoot {
  if (overlayRoot?.host.isConnected) return overlayRoot;
  const host = document.createElement('div');
  host.setAttribute('data-stept-drive', '');
  host.style.cssText = 'position:fixed;inset:0;z-index:2147483644;pointer-events:none;';
  const root = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    .ring { position: fixed; border: 2px solid #6366f1; border-radius: 10px;
      box-shadow: 0 0 0 5px rgba(99,102,241,0.25); transition: all 0.12s ease-out; }
    .bar { position: fixed; left: 50%; transform: translateX(-50%); top: 14px;
      background: #4f46e5; color: #fff; font: 600 12px/1.3 ui-sans-serif, system-ui, sans-serif;
      padding: 7px 14px; border-radius: 9999px; box-shadow: 0 6px 16px rgba(15,23,42,0.25);
      max-width: 70vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    @media (prefers-reduced-motion: reduce) { .ring { transition: none; } }
  `;
  root.appendChild(style);
  (document.body ?? document.documentElement).appendChild(host);
  overlayRoot = root;
  return root;
}

function highlight(step: TourStep, ms: number): DriverResolveResult {
  const r = resolveStepTarget(document, stepLike(step));
  const el = r.element;
  if (!el) return { found: false };
  const rect = el.getBoundingClientRect();
  const root = ensureOverlay();
  root.querySelector('.ring')?.remove();
  const ring = document.createElement('div');
  ring.className = 'ring';
  ring.style.left = `${rect.x - 5}px`;
  ring.style.top = `${rect.y - 5}px`;
  ring.style.width = `${rect.width + 10}px`;
  ring.style.height = `${rect.height + 10}px`;
  root.appendChild(ring);
  setTimeout(() => ring.remove(), Math.max(120, ms));
  return { found: true, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

function banner(text: string, ms: number): DriverResolveResult {
  const root = ensureOverlay();
  root.querySelector('.bar')?.remove();
  if (!text) return { found: true };
  const bar = document.createElement('div');
  bar.className = 'bar';
  bar.textContent = text;
  root.appendChild(bar);
  if (ms > 0) setTimeout(() => bar.remove(), ms);
  return { found: true };
}
