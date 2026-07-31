import { buildTarget, type Target } from '@stept/dom-capture';
import type { BgToContent, ContentToBg } from '../messages';
import { isSensitiveFieldAttrs, looksLikeSecret } from '../secret-redaction';
import type { RawEvent } from '../types';

/** Capture-phase recorder, ported from the old repo's `recorder.content.ts`.
 *
 * ISOLATED world, document-level listeners at `document_start` → they run
 * BEFORE the app's own handlers and therefore observe the PRE-action DOM.
 * Captures pointer gestures (click/dblclick/contextmenu), coalesced typing
 * (debounced in-page, secrets masked before they ever leave), select/check/
 * upload changes, meaningful key combos, throttled scroll, hover-reveal chains
 * and SPA route changes. Element identity comes from `@stept/dom-capture`'s
 * `buildTarget`, which also walks the same-origin frame path and the shadow
 * host chain — the recorder never rolls its own selectors.
 */
export default defineContentScript({
  matches: ['<all_urls>'],
  allFrames: true,
  runAt: 'document_start',
  main() {
    // Idempotency guard: this script is injected BOTH by the manifest
    // (document_start) AND re-injected by startRecording's executeScript. Same
    // extension → same isolated world → shared globalThis. Without the guard
    // both instances add listeners and EVERY event is captured twice.
    const g = globalThis as unknown as { __steptRecorderInit?: boolean };
    if (g.__steptRecorderInit) return;
    g.__steptRecorderInit = true;

    let recording = false;
    let pendingDownTarget: Target | null = null;
    let pendingCaptureToken: string | null = null;

    const send = (msg: ContentToBg) => {
      try {
        chrome.runtime.sendMessage(msg);
      } catch {
        /* SW asleep or context invalidated — background keeps what it has */
      }
    };

    const emit = (event: RawEvent, captureToken?: string) =>
      send({ type: 'event', event, captureToken });

    /** Document title for the step's page context; cheap and safe in any frame. */
    const pageTitle = (): string | undefined => {
      const t = document.title?.trim();
      return t ? t.slice(0, 200) : undefined;
    };

    chrome.runtime.onMessage.addListener((msg: BgToContent) => {
      if (msg?.type === 'set-recording') {
        recording = msg.recording;
        if (recording) armSpaWatcher();
      }
    });
    send({ type: 'content-ready' });

    const capture = (el: Element): Target | null => {
      try {
        // buildTarget climbs to the enclosing interactive control (an
        // <svg><path> inside a button has no durable identity of its own) and
        // records the frame path + shadow host chain.
        return buildTarget(el);
      } catch {
        return null;
      }
    };

    const targetOf = (e: Event): Element | null => {
      const path = e.composedPath();
      const el = path[0];
      return el instanceof Element ? el : (e.target as Element | null);
    };

    // ---- pointerdown (capture): the pre-action snapshot moment. The
    // screenshot is triggered HERE, before the app's click handlers repaint —
    // a post-click capture shows the RESULT state, not the state the user
    // acted on (the legacy pipeline's core trick). ----
    document.addEventListener(
      'pointerdown',
      (e) => {
        if (!recording) return;
        const el = targetOf(e);
        if (!el) return;
        pendingDownTarget = capture(el);
        // One token per gesture: the pre-capture taken now may only ever attach
        // to the pointer event that echoes this token.
        pendingCaptureToken = `pc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        send({ type: 'pre-capture', token: pendingCaptureToken });
      },
      { capture: true, passive: true },
    );

    const pointerEmit = (action: 'click' | 'dblclick' | 'context', ev: MouseEvent) => {
      if (!recording) return;
      const el = targetOf(ev);
      if (!el) return;
      const context = pendingDownTarget ?? capture(el);
      pendingDownTarget = null;
      const captureToken = pendingCaptureToken ?? undefined;
      pendingCaptureToken = null;
      if (!context) return;
      flushInputNow();
      emit(
        {
          kind: 'pointer',
          t: Date.now(),
          tabId: 0,
          frameId: 0,
          action,
          point: { x: ev.clientX, y: ev.clientY },
          modifiers: modifiersOf(ev),
          button: ev.button === 2 ? 'right' : ev.button === 1 ? 'middle' : 'left',
          context,
          url: location.href,
          pageTitle: pageTitle(),
        },
        captureToken,
      );
    };

    document.addEventListener('click', (e) => pointerEmit('click', e), { capture: true, passive: true });
    document.addEventListener('dblclick', (e) => pointerEmit('dblclick', e), { capture: true, passive: true });
    document.addEventListener('contextmenu', (e) => pointerEmit('context', e), { capture: true, passive: true });

    // ---- input: full-value snapshots, debounced in-page (one message per
    // pause/blur instead of one per keystroke — the compiler still coalesces) ----
    let inputTimer: number | null = null;
    let pendingInputEvent: RawEvent | null = null;

    const flushInputNow = () => {
      if (inputTimer) window.clearTimeout(inputTimer);
      inputTimer = null;
      if (pendingInputEvent) {
        emit(pendingInputEvent);
        pendingInputEvent = null;
      }
    };

    document.addEventListener(
      'input',
      (e) => {
        if (!recording) return;
        const el = targetOf(e);
        if (
          !el ||
          !(
            el instanceof HTMLInputElement ||
            el instanceof HTMLTextAreaElement ||
            (el as HTMLElement).isContentEditable
          )
        )
          return;
        if (el instanceof HTMLInputElement && el.type === 'file') return; // handled by 'change'
        const context = capture(el);
        if (!context) return;
        const value =
          el instanceof HTMLElement && el.isContentEditable
            ? el.innerText
            : (el as HTMLInputElement).value;
        // Field-level (password/cc/OTP/api_key-ish names) OR value-level (known
        // token shapes + high-entropy blobs) — a revealed API key in a plain
        // text input must never enter the recording.
        const isSecret =
          (el instanceof HTMLInputElement &&
            (isSensitiveFieldAttrs('input', {
              type: el.type || '',
              name: el.name || '',
              autocomplete: el.getAttribute('autocomplete') || '',
              id: el.id || '',
            }) ||
              /cc-|card|cvc|cvv/.test(el.autocomplete))) ||
          looksLikeSecret(value);
        pendingInputEvent = {
          kind: 'input',
          t: Date.now(),
          tabId: 0,
          frameId: 0,
          context,
          value: isSecret ? '' : value,
          secret: isSecret,
          pageTitle: pageTitle(),
        };
        if (inputTimer) window.clearTimeout(inputTimer);
        inputTimer = window.setTimeout(flushInputNow, 400);
      },
      { capture: true, passive: true },
    );
    document.addEventListener('blur', flushInputNow, { capture: true, passive: true });

    // ---- change: native selects, checkboxes/radios, and FILE INPUTS ----
    document.addEventListener(
      'change',
      (e) => {
        if (!recording) return;
        const el = targetOf(e);
        if (el instanceof HTMLInputElement && el.type === 'file') {
          const context = capture(el);
          const fileName = el.files?.[0]?.name;
          if (context && fileName) {
            flushInputNow();
            emit({
              kind: 'upload',
              t: Date.now(),
              tabId: 0,
              frameId: 0,
              context,
              fileName,
              pageTitle: pageTitle(),
            });
          }
          return;
        }
        if (el instanceof HTMLSelectElement) {
          const context = capture(el);
          if (context) {
            flushInputNow();
            emit({
              kind: 'select',
              t: Date.now(),
              tabId: 0,
              frameId: 0,
              context,
              value: el.value,
              label: el.selectedOptions[0]?.text,
              pageTitle: pageTitle(),
            });
          }
        } else if (el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio')) {
          const context = capture(el);
          if (context) {
            flushInputNow();
            emit({
              kind: 'check',
              t: Date.now(),
              tabId: 0,
              frameId: 0,
              context,
              checked: el.checked,
              pageTitle: pageTitle(),
            });
          }
        }
      },
      { capture: true, passive: true },
    );

    // ---- keydown: Enter flushes typing; shortcuts outside fields become steps ----
    document.addEventListener(
      'keydown',
      (e) => {
        if (!recording) return;
        const el = document.activeElement;
        const inField =
          el instanceof HTMLInputElement ||
          el instanceof HTMLTextAreaElement ||
          (el as HTMLElement | null)?.isContentEditable === true;
        const meaningful = e.key === 'Enter' || e.key === 'Escape' || e.key === 'Tab' || e.ctrlKey || e.metaKey;
        if (inField && e.key === 'Enter') {
          flushInputNow();
          emit({
            kind: 'key',
            t: Date.now(),
            tabId: 0,
            frameId: 0,
            keys: 'Enter',
            context: el ? (capture(el) ?? undefined) : undefined,
          });
          return;
        }
        if (!meaningful || inField) return;
        emit({
          kind: 'key',
          t: Date.now(),
          tabId: 0,
          frameId: 0,
          keys: keyCombo(e),
          context: el ? (capture(el) ?? undefined) : undefined,
        });
      },
      { capture: true, passive: true },
    );

    // ---- scroll: throttled to the settled position (absorbed by the
    // compiler, but recorded so future passes can use it) ----
    let scrollTimer: number | null = null;
    document.addEventListener(
      'scroll',
      () => {
        if (!recording || window !== window.top) return;
        if (scrollTimer) window.clearTimeout(scrollTimer);
        scrollTimer = window.setTimeout(() => {
          emit({
            kind: 'scroll',
            t: Date.now(),
            tabId: 0,
            frameId: 0,
            x: window.scrollX,
            y: window.scrollY,
          });
        }, 600);
      },
      { capture: true, passive: true },
    );

    // ---- hover-reveal: emit a hover step ONLY when hovering makes NEW dom
    // appear (a menu opens, a row's action icons fade in). A passive hover is
    // never a step. This is the reveal chain (hover row → click the icon that
    // appeared) that neither a click nor an input event captures, so the
    // following click's target would otherwise be unreachable on replay. ----
    let hoverCandidate: Element | null = null;
    let hoverObserver: MutationObserver | null = null;
    let hoverTimer: number | null = null;

    /** Only EXPLICIT menu/disclosure triggers are watched — an element that
     * announces it opens something, a <summary>, or a menu/row container.
     * Deliberately NOT "any interactive element": hovering a plain link must
     * never arm a watcher, or every recording fills with phantom hovers. */
    const isHoverTrigger = (el: Element): boolean =>
      !!el.matches?.(
        '[aria-haspopup],[aria-expanded],[data-hover],summary,[role="menu"],[role="menubar"],[role="menuitem"],tr,[role="row"]',
      );

    const clearHoverWatch = () => {
      hoverObserver?.disconnect();
      hoverObserver = null;
      if (hoverTimer) window.clearTimeout(hoverTimer);
      hoverTimer = null;
    };

    /** A revealed node counts as a real menu/popup only if it IS a popup
     * surface or contains ≥2 actionable items — a single incidental
     * hover-preview (a tooltip) is not a reveal chain. */
    const isMenuReveal = (node: Element): boolean => {
      if (
        node.matches?.(
          '[role="menu"],[role="listbox"],[role="dialog"],[role="tooltip"],[role="grid"],[popover],menu',
        )
      )
        return true;
      if (node.closest?.('[role="menu"],[role="listbox"],[role="dialog"]')) return true;
      const self = node.matches?.('a,button,[role="menuitem"],[role="option"]') ? 1 : 0;
      const inside = node.querySelectorAll?.('a,button,[role="menuitem"],[role="option"]').length ?? 0;
      return self + inside >= 2;
    };

    const armHoverWatch = (el: Element) => {
      if (!document.body) return;
      clearHoverWatch();
      hoverCandidate = el;
      const revealed: string[] = [];
      hoverObserver = new MutationObserver((mutations) => {
        for (const m of mutations) {
          for (const node of m.addedNodes) {
            if (revealed.length >= 3) return;
            if (!(node instanceof Element) || !isVisibleish(node) || !isMenuReveal(node)) continue;
            const text = node.textContent?.trim().slice(0, 60);
            if (text && text.length > 1) revealed.push(text);
          }
        }
      });
      hoverObserver.observe(document.body, { childList: true, subtree: true });
      hoverTimer = window.setTimeout(() => {
        clearHoverWatch();
        if (recording && revealed.length && hoverCandidate) {
          const context = capture(hoverCandidate);
          if (context)
            emit({
              kind: 'hover',
              t: Date.now(),
              tabId: 0,
              frameId: 0,
              context,
              revealedText: revealed,
              pageTitle: pageTitle(),
            });
        }
      }, 260);
    };

    document.addEventListener(
      'mouseover',
      (e) => {
        if (!recording || window !== window.top) return;
        const el = targetOf(e);
        if (!el || el === hoverCandidate || !isHoverTrigger(el)) return;
        armHoverWatch(el);
      },
      { capture: true, passive: true },
    );

    // ---- SPA route changes: webNavigation.onCommitted misses pushState — an
    // href watcher catches client-side routing without a MAIN-world hook ----
    let spaWatcherArmed = false;
    const armSpaWatcher = () => {
      if (spaWatcherArmed || window !== window.top) return;
      spaWatcherArmed = true;
      let lastHref = location.href;
      const check = () => {
        if (location.href !== lastHref) {
          lastHref = location.href;
          if (recording)
            emit({
              kind: 'nav',
              t: Date.now(),
              tabId: 0,
              frameId: 0,
              url: location.href,
              transitionType: 'auto',
              redirect: false,
            });
        }
      };
      window.addEventListener('popstate', check, { passive: true });
      window.setInterval(check, 400);
    };
  },
});

function isVisibleish(el: Element): boolean {
  const rect = el.getBoundingClientRect?.();
  return !rect || (rect.width > 0 && rect.height > 0);
}

function modifiersOf(e: MouseEvent | KeyboardEvent): string[] {
  const m: string[] = [];
  if (e.altKey) m.push('Alt');
  if (e.ctrlKey) m.push('Control');
  if (e.metaKey) m.push('Meta');
  if (e.shiftKey) m.push('Shift');
  return m;
}

function keyCombo(e: KeyboardEvent): string {
  const parts: string[] = modifiersOf(e).map((m) => (m === 'Control' ? 'Ctrl' : m));
  if (!['Control', 'Alt', 'Meta', 'Shift'].includes(e.key)) {
    parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
  }
  return parts.join('+');
}
