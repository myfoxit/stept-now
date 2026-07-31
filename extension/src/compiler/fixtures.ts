import type { Target } from '@stept/dom-capture';
import type { RawEvent } from '../types';

/** Test fixtures for the compiler suites. Kept beside the code (not in a
 * `__tests__` folder) so the shapes stay honest when the contracts move. */

let clock = 1_000;
export function resetClock(start = 1_000): void {
  clock = start;
}
/** Monotonic timestamps so `orderEvents` has something real to sort by. */
export function tick(step = 10): number {
  clock += step;
  return clock;
}

export function target(id: string, extra: Partial<Target> = {}): Target {
  return {
    selectors: [
      { kind: 'css', value: `#${id}`, score: 0.9 },
      { kind: 'aria', value: `${id}[button]`, score: 0.85 },
    ],
    text: { content: id, exact: true },
    aria: { role: 'button', name: id },
    fingerprint: {
      elementHash: `h-${id}`,
      stableHash: `s-${id}`,
      tagPath: 'html/body/button',
      attrs: {},
      neighborText: [],
    },
    bbox: { x: 10, y: 10, w: 100, h: 30, viewport: { w: 1000, h: 800 } },
    ...extra,
  };
}

export function click(id: string, over: Partial<Extract<RawEvent, { kind: 'pointer' }>> = {}): RawEvent {
  return {
    kind: 'pointer',
    t: tick(),
    tabId: 1,
    frameId: 0,
    action: 'click',
    point: { x: 40, y: 40 },
    modifiers: [],
    button: 'left',
    context: target(id),
    url: 'https://app.example.com/home',
    ...over,
  };
}

export function typed(id: string, value: string, secret = false): RawEvent {
  return {
    kind: 'input',
    t: tick(),
    tabId: 1,
    frameId: 0,
    context: target(id),
    value,
    secret,
  };
}

export function key(keys: string, id?: string): RawEvent {
  return {
    kind: 'key',
    t: tick(),
    tabId: 1,
    frameId: 0,
    keys,
    ...(id ? { context: target(id) } : {}),
  };
}

export function nav(url: string, over: Partial<Extract<RawEvent, { kind: 'nav' }>> = {}): RawEvent {
  return {
    kind: 'nav',
    t: tick(),
    tabId: 1,
    frameId: 0,
    url,
    transitionType: 'link',
    redirect: false,
    ...over,
  };
}

export function hover(id: string): RawEvent {
  return {
    kind: 'hover',
    t: tick(),
    tabId: 1,
    frameId: 0,
    context: target(id),
    revealedText: ['Settings', 'Sign out'],
  };
}
