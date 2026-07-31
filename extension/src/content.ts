/**
 * Content script — injected on demand into the active tab via
 * chrome.scripting.executeScript (activeTab + scripting permissions).
 *
 * While recording it outlines the element under the cursor and captures clicks,
 * computing a robust selector (see selector.ts) for each. Captured steps are
 * appended to chrome.storage.local (durable across popup open/close and
 * navigation) and also announced to the popup via chrome.runtime.sendMessage.
 *
 * Kept intentionally thin: the selector logic lives in the pure, tested
 * selector module; this file only wires DOM events + chrome messaging.
 */

import { elementTextHint, generateSelector } from './selector';
import type { RecordedStep } from './payload';
import { MESSAGES, STORAGE_KEYS } from './types';

declare global {
  interface Window {
    __steptRecorderInstalled?: boolean;
  }
}

// chrome.scripting may inject this file more than once; install exactly once.
if (!window.__steptRecorderInstalled) {
  window.__steptRecorderInstalled = true;

  const HIGHLIGHT_ATTR = 'data-stept-recorder';
  const ACCENT = '#6366f1';

  let recording = false;
  let listenersAttached = false;
  let highlight: HTMLDivElement | null = null;

  const isOwnElement = (target: EventTarget | null): boolean =>
    target instanceof Element && target.getAttribute(HIGHLIGHT_ATTR) !== null;

  const isRecordable = (el: Element): boolean =>
    el !== document.body && el !== document.documentElement && !isOwnElement(el);

  const ensureHighlight = (): HTMLDivElement => {
    if (highlight) return highlight;
    const box = document.createElement('div');
    box.setAttribute(HIGHLIGHT_ATTR, 'highlight');
    box.style.cssText = [
      'position:fixed',
      'z-index:2147483647',
      'pointer-events:none',
      `border:2px solid ${ACCENT}`,
      'background:rgba(99,102,241,0.15)',
      'border-radius:3px',
      'box-shadow:0 0 0 1px rgba(255,255,255,0.5)',
      'transition:left 60ms ease,top 60ms ease,width 60ms ease,height 60ms ease',
      'display:none',
    ].join(';');
    document.documentElement.appendChild(box);
    highlight = box;
    return box;
  };

  const positionHighlight = (target: Element): void => {
    const box = ensureHighlight();
    const rect = target.getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
  };

  const hideHighlight = (): void => {
    if (highlight) highlight.style.display = 'none';
  };

  const flash = (target: Element): void => {
    const box = ensureHighlight();
    positionHighlight(target);
    box.style.background = 'rgba(34,197,94,0.35)';
    window.setTimeout(() => {
      if (highlight) highlight.style.background = 'rgba(99,102,241,0.15)';
    }, 180);
  };

  const appendStep = async (el: Element): Promise<void> => {
    const step: RecordedStep = {
      selector: generateSelector(el),
      title: elementTextHint(el),
      body: '',
      textHint: elementTextHint(el),
    };
    const data = await chrome.storage.local.get(STORAGE_KEYS.steps);
    const steps = (data[STORAGE_KEYS.steps] as RecordedStep[]) ?? [];
    steps.push(step);
    await chrome.storage.local.set({ [STORAGE_KEYS.steps]: steps });
    try {
      await chrome.runtime.sendMessage({
        type: MESSAGES.step,
        selector: step.selector,
        textHint: step.textHint ?? '',
        url: location.href,
      });
    } catch {
      // No receiver (popup closed) — the step is already persisted.
    }
  };

  const onMouseOver = (event: MouseEvent): void => {
    if (!recording) return;
    const target = event.target;
    if (target instanceof Element && isRecordable(target)) positionHighlight(target);
  };

  const onClick = (event: MouseEvent): void => {
    if (!recording) return;
    const target = event.target;
    if (!(target instanceof Element) || !isRecordable(target)) return;
    // Record the selector, then let the app handle the click normally so the
    // user can keep navigating to the next element.
    void appendStep(target);
    flash(target);
  };

  const start = (): void => {
    recording = true;
    if (listenersAttached) return;
    listenersAttached = true;
    document.addEventListener('mouseover', onMouseOver, true);
    document.addEventListener('click', onClick, true);
  };

  const stop = (): void => {
    recording = false;
    if (!listenersAttached) return;
    listenersAttached = false;
    document.removeEventListener('mouseover', onMouseOver, true);
    document.removeEventListener('click', onClick, true);
    hideHighlight();
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    const type = (message as { type?: string } | null)?.type;
    if (type === MESSAGES.start) {
      start();
      sendResponse({ ok: true, url: location.href });
    } else if (type === MESSAGES.stop) {
      stop();
      sendResponse({ ok: true });
    } else if (type === MESSAGES.ping) {
      sendResponse({ ok: true, recording });
    }
    return true;
  });

  // If the popup re-injects us into a page while a recording is already active
  // (e.g. after navigation), resume automatically.
  void chrome.storage.local.get(STORAGE_KEYS.recording).then((data) => {
    if (data[STORAGE_KEYS.recording]) start();
  });
}
