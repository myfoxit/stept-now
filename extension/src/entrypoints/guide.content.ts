import { interactiveAncestor } from '@stept/dom-capture';
import { resolveStepTarget } from '../dom/resolve-step';
import { placeTooltip, type GuideActionSpec, type GuideStepPayload } from '../guide/guide-core';
import type { BgToGuideContent, ContentToBg } from '../messages';

/** Guide-mode overlay: spotlights the recorded element on the live page and
 * coaches the user through performing the step THEMSELVES. Ported near-verbatim
 * from the old repo's `guide.content.ts`; the adaptations are that resolution
 * goes through the shared dom-capture cascade (`resolveStepTarget`) and that
 * completion detection is derived from step schema v2 (`actionSpecFor`).
 *
 * All UI lives in an open shadow root so page CSS can't restyle it; the veil
 * never intercepts pointer events, so the page stays fully usable — including
 * the very click the user is being asked to make.
 */

/** High, but below anything a page's own modal is likely to claim. */
const Z_INDEX = 2147483645;
/** Keep trying to resolve this long before declaring the element missing. */
const RESOLVE_TIMEOUT_MS = 10_000;
const RESOLVE_INTERVAL_MS = 400;
const HOLE_PAD = 6;

export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: false,
  runAt: 'document_idle',
  main() {
    // Injected both by the manifest and defensively by the background — same
    // isolated world, so guard like the recorder does.
    const g = globalThis as unknown as { __steptGuideInit?: boolean };
    if (g.__steptGuideInit) return;
    g.__steptGuideInit = true;

    let session: GuideSession | null = null;

    chrome.runtime.onMessage.addListener((msg: BgToGuideContent) => {
      if (!msg || (msg.type !== 'guide-show' && msg.type !== 'guide-hide')) return;
      session?.destroy();
      session = null;
      if (msg.type === 'guide-show') session = new GuideSession(msg.step);
    });
  },
});

function send(msg: ContentToBg): void {
  try {
    chrome.runtime.sendMessage(msg);
  } catch {
    /* SW asleep — the background re-drives the step on the next navigation */
  }
}

/** One step's overlay lifetime: resolve → spotlight → observe → report. */
class GuideSession {
  private readonly step: GuideStepPayload;
  private readonly spec: GuideActionSpec;
  private el: Element | null = null;
  private host: HTMLDivElement | null = null;
  private root!: ShadowRoot;
  private veilHole: SVGRectElement | null = null;
  private veil: SVGSVGElement | null = null;
  private ring: HTMLDivElement | null = null;
  private tip: HTMLDivElement | null = null;
  private hintEl: HTMLDivElement | null = null;
  private raf = 0;
  private destroyed = false;
  private completed = false;
  private typed = false;
  private notFoundSent = false;
  private delayTimer = 0;
  private reresolveTimer = 0;
  private lastRect: DOMRect | null = null;
  private readonly cleanups: Array<() => void> = [];

  constructor(step: GuideStepPayload) {
    this.step = step;
    // resolved in the background (from the full step), never re-derived here —
    // the payload is a lossy projection and would mis-classify input steps.
    this.spec = step.spec;
    this.mount();
    if (step.target || step.selector) {
      void this.resolveLoop();
    } else {
      this.renderCard();
      this.armDelay();
    }
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    clearTimeout(this.delayTimer);
    clearInterval(this.reresolveTimer);
    for (const undo of this.cleanups.splice(0)) undo();
    this.host?.remove();
    this.host = null;
  }

  /** The recorded target, re-resolved with the shared cascade. The page may
   * still be rendering (SPA), so poll — and if it never shows up, tell the
   * engine (the panel offers Skip) but KEEP looking: a late appearance emits
   * `found` and the spotlight lights up without user intervention. */
  private async resolveLoop(): Promise<void> {
    const startedAt = Date.now();
    for (;;) {
      if (this.destroyed) return;
      const r = resolveStepTarget(document, {
        target: this.step.target,
        selector: this.step.selector,
        fallbackSelectors: this.step.fallbackSelectors,
        textHint: this.step.textHint,
      });
      if (r.element) {
        this.el = r.element;
        if (this.notFoundSent) send({ type: 'guide-event', event: 'found', index: this.step.index });
        this.onResolved();
        return;
      }
      if (!this.notFoundSent && Date.now() - startedAt > RESOLVE_TIMEOUT_MS) {
        this.notFoundSent = true;
        send({ type: 'guide-event', event: 'notfound', index: this.step.index });
        this.renderCard(true);
      }
      await new Promise((res) => setTimeout(res, RESOLVE_INTERVAL_MS));
    }
  }

  private onResolved(): void {
    const el = this.el;
    if (!el || this.destroyed) return;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    try {
      el.scrollIntoView({
        block: 'center',
        inline: 'nearest',
        behavior: reduced ? 'instant' : ('smooth' as ScrollBehavior),
      });
    } catch {
      /* detached mid-flight — the re-resolve watchdog recovers */
    }
    this.renderSpotlight();
    this.armActionListeners();
    this.armDelay();
    this.trackPosition();
    // An SPA can swap the node out from under a live spotlight; quietly
    // re-resolve so the ring follows the element's replacement.
    this.reresolveTimer = window.setInterval(() => {
      if (this.destroyed || this.completed) return;
      if (this.el && !this.el.isConnected) {
        const r = resolveStepTarget(document, {
          target: this.step.target,
          selector: this.step.selector,
          fallbackSelectors: this.step.fallbackSelectors,
          textHint: this.step.textHint,
        });
        if (r.element) this.el = r.element;
      }
    }, 900);
  }

  // ---- completion detection ------------------------------------------------

  private hits(evTarget: EventTarget | null, path?: EventTarget[]): boolean {
    const el = this.el;
    if (!el) return false;
    if (path?.includes(el)) return true;
    if (evTarget instanceof Node && (el === evTarget || el.contains(evTarget))) return true;
    // a click can land on an inner glyph of the recorded control, or on the
    // control wrapping the recorded inner node — accept both directions
    if (evTarget instanceof Element) {
      const control = interactiveAncestor(evTarget);
      if (control === el || control.contains(el) || el.contains(control)) return true;
    }
    return false;
  }

  private complete(): void {
    if (this.completed || this.destroyed) return;
    this.completed = true;
    // Send FIRST: the action may navigate the page away and the message must
    // beat the unload. The success flash is best-effort decoration.
    send({ type: 'guide-event', event: 'advance', index: this.step.index });
    this.flashSuccess();
  }

  private armDelay(): void {
    if (this.spec.on !== 'delay') return;
    this.delayTimer = window.setTimeout(() => this.complete(), this.spec.ms);
  }

  private armActionListeners(): void {
    const on = <K extends keyof WindowEventMap>(type: K, fn: (ev: WindowEventMap[K]) => void) => {
      window.addEventListener(type, fn, { capture: true, passive: true });
      this.cleanups.push(() =>
        window.removeEventListener(type, fn, { capture: true } as EventListenerOptions),
      );
    };

    if (this.spec.on === 'pointerdown') {
      on('pointerdown', (ev) => {
        if (this.hits(ev.target, ev.composedPath())) this.complete();
      });
      // custom controls (role=switch, listbox rows) sometimes only fire change
      on('change', (ev) => {
        if (this.hits(ev.target)) this.complete();
      });
    } else if (this.spec.on === 'input') {
      on('input', (ev) => {
        if (this.hits(ev.target)) {
          this.typed = true;
          this.setHint('Press Enter or click away when done.');
        }
      });
      on('change', (ev) => {
        if (this.hits(ev.target)) this.complete();
      });
      on('keydown', (ev) => {
        if (this.typed && ev.key === 'Enter' && this.hits(ev.target)) this.complete();
      });
      on('focusout', (ev) => {
        if (this.typed && this.hits(ev.target)) this.complete();
      });
    }
  }

  // ---- rendering -----------------------------------------------------------

  private mount(): void {
    const host = document.createElement('div');
    host.setAttribute('data-stept-guide', '');
    host.style.cssText = `position:fixed;inset:0;z-index:${Z_INDEX};pointer-events:none;`;
    // open shadow root: page CSS still can't leak in, and devtools/e2e can see
    // the overlay. Closed would buy no real protection — the page can remove
    // the host node either way.
    const root = host.attachShadow({ mode: 'open' });
    root.appendChild(styles());
    this.host = host;
    this.root = root;
    (document.body ?? document.documentElement).appendChild(host);
  }

  /** Veil with a punched-out hole over the target (SVG mask) + pulsing ring. */
  private renderSpotlight(): void {
    const ns = 'http://www.w3.org/2000/svg';
    const veil = document.createElementNS(ns, 'svg');
    veil.setAttribute('class', 'veil');
    veil.setAttribute('width', '100%');
    veil.setAttribute('height', '100%');
    const defs = document.createElementNS(ns, 'defs');
    const mask = document.createElementNS(ns, 'mask');
    const maskId = 'stept-guide-mask';
    mask.setAttribute('id', maskId);
    const full = document.createElementNS(ns, 'rect');
    full.setAttribute('x', '0');
    full.setAttribute('y', '0');
    full.setAttribute('width', '100%');
    full.setAttribute('height', '100%');
    full.setAttribute('fill', '#fff');
    const hole = document.createElementNS(ns, 'rect');
    hole.setAttribute('rx', '10');
    hole.setAttribute('fill', '#000');
    mask.append(full, hole);
    defs.appendChild(mask);
    const shade = document.createElementNS(ns, 'rect');
    shade.setAttribute('x', '0');
    shade.setAttribute('y', '0');
    shade.setAttribute('width', '100%');
    shade.setAttribute('height', '100%');
    shade.setAttribute('fill', 'rgba(15, 23, 42, 0.42)');
    shade.setAttribute('mask', `url(#${maskId})`);
    veil.append(defs, shade);

    const ring = document.createElement('div');
    ring.className = 'ring pulse';

    this.veil = veil;
    this.veilHole = hole;
    this.ring = ring;
    this.root.append(veil, ring);
    this.renderTip(false);
  }

  /** Centered instruction card — steps with no element (modal/banner/wait) and
   * the not-found state. */
  private renderCard(missing = false): void {
    this.veil?.remove();
    this.ring?.remove();
    this.veil = null;
    this.veilHole = null;
    this.ring = null;
    this.renderTip(true, missing);
    if (this.tip) {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const r = this.tip.getBoundingClientRect();
      this.tip.style.left = `${Math.max(10, (vw - r.width) / 2)}px`;
      this.tip.style.top = `${Math.max(10, vh - r.height - 28)}px`;
    }
  }

  private renderTip(floating: boolean, missing = false): void {
    this.tip?.remove();
    const s = this.step;
    const tip = document.createElement('div');
    tip.className = 'tip enter';
    tip.setAttribute('role', 'dialog');
    tip.setAttribute('aria-label', `${s.tourName}: step ${s.index + 1} of ${s.total}`);

    const head = document.createElement('div');
    head.className = 'head';
    const brand = document.createElement('span');
    brand.className = 'brand';
    brand.textContent = s.tourName || 'Stept';
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = `${s.index + 1} / ${s.total}`;
    const x = document.createElement('button');
    x.className = 'x';
    x.setAttribute('aria-label', 'Exit guide');
    x.textContent = '×';
    x.addEventListener('click', () => send({ type: 'guide-event', event: 'stop', index: s.index }));
    head.append(brand, count, x);

    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('i');
    fill.style.width = `${Math.round((s.index / Math.max(1, s.total)) * 100)}%`;
    bar.appendChild(fill);

    const lead = document.createElement('div');
    lead.className = 'lead';
    lead.textContent = s.instruction;

    tip.append(head, bar, lead);

    if (missing) {
      const warn = document.createElement('div');
      warn.className = 'warn';
      warn.textContent =
        'This element is not on the page right now — it may have moved since the recording. Do the step manually, or skip it.';
      tip.appendChild(warn);
    } else if (s.detail || s.body) {
      const sub = document.createElement('div');
      sub.className = 'sub';
      sub.textContent = s.body ? stripMarkdown(s.body) : (s.detail ?? '');
      tip.appendChild(sub);
    }

    if (s.value) {
      const chip = document.createElement('div');
      chip.className = 'chip';
      const text = document.createElement('span');
      text.textContent = s.value;
      const copy = document.createElement('button');
      copy.className = 'copy';
      copy.textContent = 'Copy';
      copy.addEventListener('click', () => {
        void navigator.clipboard
          ?.writeText(s.value ?? '')
          .then(() => {
            copy.textContent = 'Copied';
            setTimeout(() => (copy.textContent = 'Copy'), 1400);
          })
          .catch(() => {});
      });
      chip.append(text, copy);
      tip.appendChild(chip);
    }

    const row = document.createElement('div');
    row.className = 'row';
    const back = document.createElement('button');
    back.className = 'b';
    back.textContent = 'Back';
    back.disabled = s.index === 0;
    back.addEventListener('click', () => send({ type: 'guide-event', event: 'back', index: s.index }));
    const spacer = document.createElement('div');
    spacer.className = 'spacer';
    const next = document.createElement('button');
    next.className = 'b primary';
    next.textContent = s.index + 1 >= s.total ? 'Finish' : s.waitsForAction ? 'Skip' : 'Next';
    next.addEventListener('click', () => this.complete());
    row.append(back, spacer, next);
    tip.appendChild(row);

    const hint = document.createElement('div');
    hint.className = 'hint';
    if (s.waitsForAction && !missing) {
      const dot = document.createElement('span');
      dot.className = 'dot';
      const label = document.createElement('span');
      label.textContent = 'Continues automatically when you do it';
      hint.append(dot, label);
    }
    tip.appendChild(hint);
    this.hintEl = hint;

    this.tip = tip;
    this.root.appendChild(tip);
    if (floating) tip.style.visibility = 'visible';
  }

  private setHint(text: string): void {
    if (!this.hintEl) return;
    this.hintEl.textContent = '';
    const dot = document.createElement('span');
    dot.className = 'dot';
    const label = document.createElement('span');
    label.textContent = text;
    this.hintEl.append(dot, label);
  }

  private flashSuccess(): void {
    this.ring?.classList.remove('pulse');
    this.ring?.classList.add('ok');
  }

  /** Follow the element every frame: hole, ring and tooltip stay glued to it
   * through scrolling, resizes and layout shifts. */
  private trackPosition(): void {
    const stepFrame = () => {
      if (this.destroyed) return;
      const el = this.el;
      if (el && el.isConnected && this.ring && this.veilHole && this.tip) {
        const r = el.getBoundingClientRect();
        const changed =
          !this.lastRect ||
          Math.abs(r.x - this.lastRect.x) > 0.5 ||
          Math.abs(r.y - this.lastRect.y) > 0.5 ||
          Math.abs(r.width - this.lastRect.width) > 0.5 ||
          Math.abs(r.height - this.lastRect.height) > 0.5;
        if (changed) {
          this.lastRect = r;
          const x = r.x - HOLE_PAD;
          const y = r.y - HOLE_PAD;
          const w = r.width + HOLE_PAD * 2;
          const h = r.height + HOLE_PAD * 2;
          this.veilHole.setAttribute('x', String(x));
          this.veilHole.setAttribute('y', String(y));
          this.veilHole.setAttribute('width', String(Math.max(0, w)));
          this.veilHole.setAttribute('height', String(Math.max(0, h)));
          this.ring.style.left = `${x}px`;
          this.ring.style.top = `${y}px`;
          this.ring.style.width = `${Math.max(0, w)}px`;
          this.ring.style.height = `${Math.max(0, h)}px`;
          const tr = this.tip.getBoundingClientRect();
          const place = placeTooltip(
            { x, y, w, h },
            { w: tr.width || 296, h: tr.height || 150 },
            { w: window.innerWidth, h: window.innerHeight },
          );
          this.tip.style.left = `${place.x}px`;
          this.tip.style.top = `${place.y}px`;
        }
      }
      this.raf = requestAnimationFrame(stepFrame);
    };
    this.raf = requestAnimationFrame(stepFrame);
  }
}

/** The overlay is DOM-built, never innerHTML'd — markdown is flattened to text
 * rather than rendered, so a step body can never inject markup into a host page. */
function stripMarkdown(md: string): string {
  return md
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 400);
}

function styles(): HTMLStyleElement {
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
    .veil { position: fixed; inset: 0; pointer-events: none; }
    .ring {
      position: fixed; pointer-events: none; border: 2px solid #6366f1; border-radius: 10px;
      box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.28), 0 0 24px rgba(99, 102, 241, 0.35);
      transition: opacity 0.2s;
    }
    .ring.pulse { animation: guide-pulse 1.6s ease-in-out infinite; }
    .ring.ok { border-color: #22c55e; box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.3); animation: none; }
    @keyframes guide-pulse {
      0%, 100% { box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.28), 0 0 24px rgba(99, 102, 241, 0.35); }
      50% { box-shadow: 0 0 0 9px rgba(99, 102, 241, 0.12), 0 0 32px rgba(99, 102, 241, 0.45); }
    }
    .tip {
      position: fixed; pointer-events: auto; width: 296px; max-width: calc(100vw - 20px);
      background: #ffffff; color: #111827; border: 1px solid #e5e7eb; border-radius: 12px;
      box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05);
      padding: 12px 14px; font-size: 13px; line-height: 1.45;
    }
    .tip.enter { animation: guide-in 0.22s cubic-bezier(0.21, 1.02, 0.73, 1); }
    @keyframes guide-in { from { opacity: 0; transform: translateY(6px) scale(0.98); } to { opacity: 1; transform: none; } }
    .head { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
    .brand { font-size: 11px; font-weight: 650; color: #4f46e5; letter-spacing: 0.01em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 170px; }
    .count { margin-left: auto; font-size: 11px; font-weight: 600; color: #6b7280; font-variant-numeric: tabular-nums; }
    .x { display: grid; place-items: center; width: 22px; height: 22px; border: 0; background: transparent; color: #9ca3af; border-radius: 6px; cursor: pointer; padding: 0; font-size: 15px; }
    .x:hover { background: #f3f4f6; color: #111827; }
    .bar { height: 3px; border-radius: 9999px; background: #eef2ff; overflow: hidden; margin-bottom: 10px; }
    .bar > i { display: block; height: 100%; border-radius: 9999px; background: #4f46e5; transition: width 0.3s ease; }
    .lead { font-weight: 600; overflow-wrap: anywhere; }
    .sub { color: #6b7280; font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }
    .warn { color: #b45309; font-size: 12px; margin-top: 3px; }
    .chip { display: flex; align-items: center; gap: 8px; margin-top: 8px; padding: 6px 9px; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    .chip > span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .copy { flex: none; border: 0; background: transparent; color: #4f46e5; font-size: 11px; font-weight: 600; cursor: pointer; padding: 2px 4px; border-radius: 5px; font-family: inherit; }
    .copy:hover { background: #eef2ff; }
    .row { display: flex; align-items: center; gap: 6px; margin-top: 11px; }
    .hint { font-size: 11px; color: #9ca3af; margin-top: 9px; display: flex; align-items: center; gap: 6px; min-height: 14px; }
    .dot { width: 6px; height: 6px; border-radius: 9999px; background: #4f46e5; animation: guide-pulse-dot 1.4s ease-in-out infinite; }
    @keyframes guide-pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
    button.b { display: inline-flex; align-items: center; justify-content: center; gap: 5px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; color: #111827; padding: 6px 11px; font-size: 12px; font-weight: 500; cursor: pointer; }
    button.b:hover { background: #f3f4f6; }
    button.b:disabled { opacity: 0.45; cursor: default; }
    button.b.primary { background: #4f46e5; border-color: #4f46e5; color: #fff; }
    button.b.primary:hover { opacity: 0.92; }
    .spacer { flex: 1; }
    @media (prefers-color-scheme: dark) {
      .tip { background: #1e293b; color: #f1f5f9; border-color: #334155; }
      .x:hover { background: #334155; color: #f1f5f9; }
      .count, .sub { color: #94a3b8; }
      .bar { background: rgba(99, 102, 241, 0.2); }
      .bar > i { background: #818cf8; }
      .brand { color: #a5b4fc; }
      .chip { background: rgba(51, 65, 85, 0.5); border-color: #334155; }
      .copy { color: #a5b4fc; }
      .copy:hover { background: rgba(99, 102, 241, 0.2); }
      button.b { background: #1e293b; border-color: #334155; color: #f1f5f9; }
      button.b:hover { background: #334155; }
      button.b.primary { background: #6366f1; border-color: #6366f1; color: #fff; }
      .hint { color: #64748b; }
      .warn { color: #fbbf24; }
    }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
  `;
  return style;
}
