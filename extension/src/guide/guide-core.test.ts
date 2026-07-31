import { describe, expect, it } from 'vitest';
import {
  actionSpecFor,
  buildGuidePayload,
  hasTemplate,
  keyComboMatches,
  panelSteps,
  placeTooltip,
  urlEffectMatches,
} from './guide-core';
import type { TourStep } from '../types';

/** Ported from the old repo's `guide-core.test.ts`, retargeted at step schema
 * v2 (advance rules instead of the old 16 step types). */

const step = (over: Partial<TourStep> = {}): TourStep => ({
  id: 's1',
  type: 'tooltip',
  selector: '#go',
  fallback_selectors: [],
  text_hint: 'Go',
  target: null,
  title: 'Click on “Go”',
  body: '',
  placement: 'auto',
  advance: { on: 'element_click' },
  ...over,
});

describe('actionSpecFor', () => {
  it('maps each advance rule to the event that completes it', () => {
    expect(actionSpecFor(step())).toEqual({ on: 'pointerdown' });
    expect(actionSpecFor(step({ advance: { on: 'input' } }))).toEqual({ on: 'input' });
    expect(actionSpecFor(step({ advance: { on: 'button' } }))).toEqual({ on: 'manual' });
    expect(actionSpecFor(step({ advance: { on: 'delay', delay_ms: 1500 } }))).toEqual({
      on: 'delay',
      ms: 1500,
    });
  });

  it('derives action steps from their action kind, not their advance rule', () => {
    expect(
      actionSpecFor(step({ type: 'action', action: { kind: 'click' }, advance: { on: 'button' } })),
    ).toEqual({ on: 'pointerdown' });
    expect(
      actionSpecFor(step({ type: 'action', action: { kind: 'fill', value: 'x' }, advance: { on: 'button' } })),
    ).toEqual({ on: 'input' });
    expect(
      actionSpecFor(step({ type: 'action', action: { kind: 'navigate', url: 'https://a.example' } })),
    ).toEqual({ on: 'manual' });
  });

  it('leaves wait / modal / banner steps to the engine and the Next button', () => {
    expect(
      actionSpecFor(step({ type: 'wait', wait: { for: 'url', url_pattern: '*', timeout_ms: 1000 } })),
    ).toEqual({ on: 'manual' });
    expect(actionSpecFor(step({ type: 'modal', advance: { on: 'element_click' } }))).toEqual({
      on: 'manual',
    });
    expect(actionSpecFor(step({ type: 'banner', advance: { on: 'delay', delay_ms: 900 } }))).toEqual({
      on: 'delay',
      ms: 900,
    });
  });
});

describe('buildGuidePayload', () => {
  it('carries the anchor and the counters the overlay renders', () => {
    const p = buildGuidePayload(step({ fallback_selectors: ['aria/Go[button]'] }), 1, 5, 'Onboarding');
    expect(p).toMatchObject({
      index: 1,
      total: 5,
      tourName: 'Onboarding',
      selector: '#go',
      textHint: 'Go',
      waitsForAction: true,
    });
    expect(p.fallbackSelectors).toEqual(['aria/Go[button]']);
  });

  it('resolves the completion spec IN the payload (the overlay must not re-derive it)', () => {
    // a fill step advances on input — reconstructing the spec from the lossy
    // payload used to classify it as a click, so it never advanced
    const fill = buildGuidePayload(
      step({ type: 'action', action: { kind: 'fill', value: 'x' }, advance: { on: 'button' } }),
      0,
      1,
      'Flow',
    );
    expect(fill.spec).toEqual({ on: 'input' });
    expect(
      buildGuidePayload(step({ advance: { on: 'delay', delay_ms: 800 } }), 0, 1, 'Flow').spec,
    ).toEqual({ on: 'delay', ms: 800 });
  });

  it('shows the literal fill value as a copyable chip', () => {
    const p = buildGuidePayload(
      step({ type: 'action', action: { kind: 'fill', value: 'cats' } }),
      0,
      2,
      'Search',
    );
    expect(p.value).toBe('cats');
    expect(p.detail).toMatch(/Enter or click away/);
  });

  it('never leaks a secret-shaped value into the page payload', () => {
    const p = buildGuidePayload(
      step({ type: 'action', action: { kind: 'fill', value: 'sk-proj-AbCdEf123456789AbCdEf09' } }),
      0,
      2,
      'Keys',
    );
    expect(p.value).toBeUndefined();
    expect(JSON.stringify(p)).not.toContain('sk-proj');
    expect(p.detail).toMatch(/private/i);
  });

  it('turns a {{variable}} placeholder into friendly copy instead of a chip', () => {
    const p = buildGuidePayload(
      step({ type: 'action', action: { kind: 'fill', value: '{{search-term}}' } }),
      0,
      1,
      'Flow',
    );
    expect(p.value).toBeUndefined();
    expect(p.detail).toContain('search-term');
  });

  it('drops the anchor for unanchored step types', () => {
    const p = buildGuidePayload(step({ type: 'modal', advance: { on: 'button' } }), 0, 3, 'Flow');
    expect(p.selector).toBeUndefined();
    expect(p.waitsForAction).toBe(false);
  });
});

describe('urlEffectMatches', () => {
  it('matches only a wait-for-url step, honouring wildcards', () => {
    const wait = step({
      type: 'wait',
      wait: { for: 'url', url_pattern: 'https://a.example/done*', timeout_ms: 1000 },
    });
    expect(urlEffectMatches(wait, 'https://a.example/done?ok=1')).toBe(true);
    expect(urlEffectMatches(wait, 'https://a.example/elsewhere')).toBe(false);
    expect(urlEffectMatches(step(), 'https://a.example/done')).toBe(false);
    expect(
      urlEffectMatches(
        step({ type: 'wait', wait: { for: 'element', selector: '#x', timeout_ms: 1000 } }),
        'https://a.example/done',
      ),
    ).toBe(false);
  });
});

describe('keyComboMatches', () => {
  it('matches bare keys and full modifier combos', () => {
    expect(keyComboMatches('Enter', { key: 'Enter' })).toBe(true);
    expect(keyComboMatches('Enter', { key: 'Enter', ctrlKey: true })).toBe(false);
    expect(keyComboMatches('Control+k', { key: 'K', ctrlKey: true })).toBe(true);
    expect(keyComboMatches('Meta+Enter', { key: 'Enter', metaKey: true })).toBe(true);
    expect(keyComboMatches('Meta+Enter', { key: 'Enter' })).toBe(false);
    expect(keyComboMatches('Shift+Tab', { key: 'Tab', shiftKey: true })).toBe(true);
    expect(keyComboMatches('', { key: 'a' })).toBe(false);
  });

  it('does not require shift when the combo omits it (typed symbols)', () => {
    expect(keyComboMatches('?', { key: '?', shiftKey: true })).toBe(true);
  });
});

describe('placeTooltip', () => {
  const TIP = { w: 296, h: 140 };
  const VIEW = { w: 800, h: 600 };

  it('prefers below the anchor, centered on it', () => {
    const p = placeTooltip({ x: 300, y: 100, w: 80, h: 30 }, TIP, VIEW);
    expect(p.side).toBe('below');
    expect(p.y).toBe(100 + 30 + 12);
    expect(p.x).toBe(300 + 40 - TIP.w / 2);
  });

  it('flips above when there is no room below', () => {
    const p = placeTooltip({ x: 100, y: 520, w: 80, h: 40 }, TIP, VIEW);
    expect(p.side).toBe('above');
    expect(p.y).toBe(520 - 12 - TIP.h);
  });

  it('clamps horizontally at the viewport edges', () => {
    expect(placeTooltip({ x: 0, y: 100, w: 20, h: 20 }, TIP, VIEW).x).toBe(10);
    expect(placeTooltip({ x: 780, y: 100, w: 20, h: 20 }, TIP, VIEW).x).toBe(VIEW.w - TIP.w - 10);
  });

  it('floats bottom-center when the anchor is off-screen or boxed in', () => {
    expect(placeTooltip({ x: 100, y: -500, w: 80, h: 30 }, TIP, VIEW).side).toBe('floating');
    // full-bleed anchor: no room below, above, left or right
    const boxed = placeTooltip({ x: 10, y: 10, w: 780, h: 580 }, TIP, VIEW);
    expect(boxed.side).toBe('floating');
    expect(boxed.y).toBe(VIEW.h - TIP.h - 24);
  });

  it('uses a side when the anchor is tall but narrow (a nav rail)', () => {
    // the ported version only knew below/above and floated here; v2 placements
    // let it dock beside the rail instead
    expect(placeTooltip({ x: 100, y: 10, w: 80, h: 580 }, TIP, VIEW).side).toBe('right');
  });

  it('honours the step’s recorded placement when it fits', () => {
    const right = placeTooltip({ x: 20, y: 300, w: 60, h: 40 }, TIP, VIEW, 12, 'right');
    expect(right.side).toBe('right');
    expect(right.x).toBe(20 + 60 + 12);
    // …and silently falls back when it does not
    expect(placeTooltip({ x: 700, y: 300, w: 60, h: 40 }, TIP, VIEW, 12, 'right').side).toBe('below');
  });
});

describe('panelSteps / hasTemplate', () => {
  it('trims steps to what the panel needs', () => {
    expect(panelSteps([step()])).toEqual([{ id: 's1', title: 'Click on “Go”', type: 'tooltip' }]);
  });

  it('detects {{var}} templates', () => {
    expect(hasTemplate('{{query}}')).toBe(true);
    expect(hasTemplate('https://a.example/{{ id }}/edit')).toBe(true);
    expect(hasTemplate('plain text')).toBe(false);
    expect(hasTemplate(undefined)).toBe(false);
  });
});
