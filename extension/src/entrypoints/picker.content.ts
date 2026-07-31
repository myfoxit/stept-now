import { buildTarget, simpleProjection } from '@stept/dom-capture';
import type { ContentToBg } from '../messages';

/** Selector picker: hover to highlight, click to capture.
 *
 * The captured element goes through the SAME `buildTarget` the recorder uses,
 * so what the panel shows (and what you paste into a step) is exactly what a
 * recorded step would have carried — ranked selectors, fallbacks and the text
 * hint from one source of truth. Used for re-targeting an existing step and for
 * the dashboard's "copy selector" flow.
 */
export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: false,
  runAt: 'document_idle',
  main() {
    const g = globalThis as unknown as { __steptPickerInit?: boolean };
    if (g.__steptPickerInit) return;
    g.__steptPickerInit = true;

    let active = false;
    let root: ShadowRoot | null = null;
    let box: HTMLDivElement | null = null;
    let label: HTMLDivElement | null = null;

    chrome.runtime.onMessage.addListener((msg: { type?: string }) => {
      if (msg?.type === 'picker-arm') start();
      else if (msg?.type === 'picker-disarm') stop();
    });

    const send = (msg: ContentToBg) => {
      try {
        chrome.runtime.sendMessage(msg);
      } catch {
        /* SW asleep — the panel re-requests state */
      }
    };

    function mount(): ShadowRoot {
      if (root?.host.isConnected) return root;
      const host = document.createElement('div');
      host.setAttribute('data-stept-picker', '');
      host.style.cssText = 'position:fixed;inset:0;z-index:2147483646;pointer-events:none;';
      const shadow = host.attachShadow({ mode: 'open' });
      const style = document.createElement('style');
      style.textContent = `
        .box { position: fixed; border: 2px solid #6366f1; background: rgba(99,102,241,0.14);
          border-radius: 4px; transition: all 0.05s linear; }
        .lbl { position: fixed; background: #4f46e5; color: #fff; border-radius: 6px;
          font: 600 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; padding: 3px 7px;
          max-width: 60vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .tip { position: fixed; left: 50%; transform: translateX(-50%); bottom: 18px;
          background: #111827; color: #fff; border-radius: 9999px; padding: 7px 15px;
          font: 500 12px/1.3 ui-sans-serif, system-ui, sans-serif; box-shadow: 0 8px 20px rgba(0,0,0,0.3); }
      `;
      const tip = document.createElement('div');
      tip.className = 'tip';
      tip.textContent = 'Click an element to capture its selector · Esc to cancel';
      box = document.createElement('div');
      box.className = 'box';
      label = document.createElement('div');
      label.className = 'lbl';
      shadow.append(style, box, label, tip);
      (document.body ?? document.documentElement).appendChild(host);
      root = shadow;
      return shadow;
    }

    function start(): void {
      if (active) return;
      active = true;
      mount();
      document.addEventListener('mousemove', onMove, true);
      document.addEventListener('click', onClick, true);
      document.addEventListener('keydown', onKey, true);
    }

    function stop(): void {
      active = false;
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey, true);
      root?.host.remove();
      root = null;
      box = null;
      label = null;
    }

    function elementAt(ev: Event): Element | null {
      const el = ev.composedPath()[0];
      return el instanceof Element ? el : null;
    }

    function onMove(ev: MouseEvent): void {
      const el = elementAt(ev);
      if (!el || !box || !label) return;
      const rect = el.getBoundingClientRect();
      box.style.left = `${rect.x}px`;
      box.style.top = `${rect.y}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      label.textContent = describe(el);
      const below = rect.y + rect.height + 4;
      label.style.left = `${Math.max(4, rect.x)}px`;
      label.style.top = `${below + 22 < window.innerHeight ? below : Math.max(4, rect.y - 22)}px`;
    }

    function onClick(ev: MouseEvent): void {
      const el = elementAt(ev);
      if (!el) return;
      ev.preventDefault();
      ev.stopPropagation();
      try {
        const projection = simpleProjection(buildTarget(el));
        send({
          type: 'picked',
          picked: {
            selector: projection.selector,
            fallbacks: projection.fallback_selectors,
            textHint: projection.text_hint,
          },
        });
      } catch {
        send({ type: 'picked', picked: null });
      }
      stop();
    }

    function onKey(ev: KeyboardEvent): void {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      send({ type: 'picked', picked: null });
      stop();
    }
  },
});

/** Short human label for the hover box: `tag#id.class` capped for readability. */
function describe(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const id = el.id ? `#${el.id}` : '';
  const cls = typeof el.className === 'string' && el.className.trim()
    ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}`
    : '';
  return `${tag}${id}${cls}`.slice(0, 80);
}
