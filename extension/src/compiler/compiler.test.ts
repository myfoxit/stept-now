import { beforeEach, describe, expect, it } from 'vitest';
import { compile, elementKey, isSyntheticSubmitClick, orderEvents } from './index';
import { click, hover, key, nav, resetClock, target, tick, typed } from './fixtures';
import type { RawEvent } from '../types';

beforeEach(() => resetClock());

describe('orderEvents', () => {
  it('restores causal order from a permuted array', () => {
    const events = [
      { kind: 'pointer', t: 30 },
      { kind: 'input', t: 10 },
      { kind: 'key', t: 20 },
    ];
    expect(orderEvents(events).map((e) => e.kind)).toEqual(['input', 'key', 'pointer']);
  });

  it('breaks a same-millisecond tie by causality: input → key → the rest', () => {
    const events = [
      { kind: 'pointer', t: 5 },
      { kind: 'key', t: 5 },
      { kind: 'input', t: 5 },
    ];
    expect(orderEvents(events).map((e) => e.kind)).toEqual(['input', 'key', 'pointer']);
  });

  it('is stable for events that agree on time and rank', () => {
    const events = [
      { kind: 'nav', t: 1, id: 'a' },
      { kind: 'nav', t: 1, id: 'b' },
    ];
    expect(orderEvents(events).map((e) => e.id)).toEqual(['a', 'b']);
  });
});

describe('step emission per event kind', () => {
  it('turns a click into a tooltip step that advances on the element click', () => {
    const { steps } = compile([click('save')]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({
      type: 'tooltip',
      selector: '#save',
      text_hint: 'save',
      title: 'Click on “save”',
      advance: { on: 'element_click' },
    });
    // the rich target travels along for the healing cascade
    expect(steps[0]?.target?.fingerprint?.elementHash).toBe('h-save');
    expect(steps[0]?.fallback_selectors).toContain('aria/save[button]');
  });

  it('turns typing into a fill action carrying the value', () => {
    const { steps } = compile([typed('email', 'ada@example.com')]);
    expect(steps[0]).toMatchObject({
      type: 'action',
      action: { kind: 'fill', value: 'ada@example.com' },
      advance: { on: 'input' },
    });
  });

  it('never emits a secret value — a masked field becomes a "type your own" tooltip', () => {
    const { steps } = compile([typed('password', '', true)]);
    expect(steps[0]?.type).toBe('tooltip');
    expect(steps[0]?.action).toBeUndefined();
    expect(steps[0]?.body).toMatch(/private/i);
    expect(JSON.stringify(steps)).not.toContain('fill');
  });

  it('maps select / check / upload onto tooltips with the right advance rule', () => {
    const events: RawEvent[] = [
      { kind: 'select', t: tick(), tabId: 1, frameId: 0, context: target('plan'), value: 'pro', label: 'Pro' },
      { kind: 'check', t: tick(), tabId: 1, frameId: 0, context: target('terms'), checked: true },
      { kind: 'upload', t: tick(), tabId: 1, frameId: 0, context: target('avatar'), fileName: 'me.png' },
    ];
    const { steps } = compile(events);
    expect(steps.map((s) => [s.type, s.advance.on, s.title])).toEqual([
      ['tooltip', 'input', 'Select “Pro” in “plan”'],
      ['tooltip', 'element_click', 'Check “terms”'],
      ['tooltip', 'element_click', 'Upload “me.png”'],
    ]);
  });

  it('turns a keypress without a target into a centred modal step', () => {
    const { steps } = compile([click('a'), key('Ctrl+K')]);
    expect(steps[1]).toMatchObject({ type: 'modal', title: 'Press Ctrl+K', placement: 'center' });
  });

  it('turns a navigation into a wait-for-url step and drops a trailing one', () => {
    const { steps } = compile([
      click('go'),
      nav('https://app.example.com/dashboard/9f2c1a7b'),
      click('next'),
      nav('https://app.example.com/done'),
    ]);
    expect(steps.map((s) => s.type)).toEqual(['tooltip', 'wait', 'tooltip']);
    expect(steps[1]?.wait).toMatchObject({
      for: 'url',
      url_pattern: 'https://app.example.com/dashboard/*',
      timeout_ms: 10000,
    });
  });

  it('treats navigation before the first action as the tour entry, not a step', () => {
    const { steps, suggestedUrlPattern } = compile([
      nav('https://app.example.com/login', { transitionType: 'typed' }),
      click('save'),
    ]);
    expect(steps.map((s) => s.type)).toEqual(['tooltip']);
    expect(suggestedUrlPattern).toBe('https://app.example.com/login*');
  });
});

describe('coalescing passes', () => {
  it('coalesces a typing burst into ONE fill step with the final value', () => {
    const { steps, sources } = compile([typed('q', 'c'), typed('q', 'ca'), typed('q', 'cat')]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.action?.value).toBe('cat');
    expect(sources[steps[0]!.id]).toEqual([0, 1, 2]);
  });

  it('flushes typing when focus moves to another field', () => {
    const { steps } = compile([typed('a', 'one'), typed('b', 'two')]);
    expect(steps.map((s) => s.action?.value)).toEqual(['one', 'two']);
  });

  it('collapses click,click,dblclick on one element into a single double-click step', () => {
    const { steps } = compile([click('row'), click('row'), click('row', { action: 'dblclick' })]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.title).toBe('Double-click “row”');
  });

  it('folds an Enter into the typing it submitted (title only — advance already covers Enter)', () => {
    const { steps, sources } = compile([typed('q', 'cats'), key('Enter', 'q')]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.title).toBe('Type “cats” into “q” and press Enter');
    expect(sources[steps[0]!.id]).toContain(1);
  });

  it('folds an Enter recorded OUT OF ORDER (before the coalesced input)', () => {
    // some sites emit the key first; both land in the same tick so orderEvents
    // cannot separate them by time — the adjacency fold is what saves it
    const enter = key('Enter', 'q');
    const input = typed('q', 'cats');
    input.t = enter.t; // same millisecond
    const { steps } = compile([enter, input]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.type).toBe('action');
    expect(steps[0]?.title).toMatch(/press Enter$/);
  });

  it('drops the browser’s synthetic submit click', () => {
    const synthetic = click('submit', {
      point: { x: 0, y: 0 },
      context: target('submit', {
        fingerprint: {
          elementHash: 'h-submit',
          stableHash: 's-submit',
          tagPath: 'html/body/form/button',
          attrs: { type: 'submit' },
          neighborText: [],
        },
      }),
    });
    expect(isSyntheticSubmitClick(synthetic)).toBe(true);
    const { steps } = compile([typed('q', 'cats'), key('Enter', 'q'), synthetic]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.type).toBe('action');
  });

  it('keeps a REAL click on a submit button (it carries pointer coordinates)', () => {
    const real = click('submit', {
      context: target('submit', {
        fingerprint: {
          elementHash: 'h-submit',
          stableHash: 's-submit',
          tagPath: 'html/body/form/button',
          attrs: { type: 'submit' },
          neighborText: [],
        },
      }),
    });
    expect(isSyntheticSubmitClick(real)).toBe(false);
    expect(compile([real]).steps).toHaveLength(1);
  });

  it('folds a hover into the action on the same element, and drops a trailing hover', () => {
    const folded = compile([hover('menu'), click('menu'), click('item')]);
    expect(folded.steps.map((s) => s.title)).toEqual([
      'Click on “menu”',
      'Click on “item”',
    ]);
    const trailing = compile([click('a'), hover('menu')]);
    expect(trailing.steps.map((s) => s.title)).toEqual(['Click on “a”']);
  });

  it('keeps a hover that revealed a DIFFERENT element (the real reveal chain)', () => {
    const { steps } = compile([hover('row'), click('row-delete'), click('confirm')]);
    expect(steps.map((s) => s.title)).toEqual([
      'Hover “row”',
      'Click on “row-delete”',
      'Click on “confirm”',
    ]);
  });

  it('absorbs scroll, download and tab events entirely', () => {
    const events: RawEvent[] = [
      { kind: 'scroll', t: tick(), tabId: 1, frameId: 0, x: 0, y: 400 },
      { kind: 'download', t: tick(), tabId: 1, frameId: 0, filename: 'report.pdf' },
      { kind: 'tab', t: tick(), tabId: 2, frameId: 0, action: 'created' },
      click('ok'),
    ];
    expect(compile(events).steps.map((s) => s.type)).toEqual(['tooltip']);
  });
});

describe('titling, overrides and sources', () => {
  it('derives a tour name from the brand and the first content step', () => {
    const { suggestedName, suggestedSlug } = compile([
      nav('https://github.com/settings', { transitionType: 'typed' }),
      click('New repository'),
    ]);
    expect(suggestedName).toBe('GitHub — Click on “New repository”');
    expect(suggestedSlug).toBe('github-click-on-new-repository');
  });

  it('applies title/body overrides and the panel’s reorder', () => {
    const { steps } = compile([click('a'), click('b')], {
      titleOverrides: { s1: 'Renamed' },
      bodyOverrides: { s2: 'Some **markdown**' },
      order: ['s2', 's1'],
    });
    expect(steps.map((s) => s.id)).toEqual(['s2', 's1']);
    expect(steps.find((s) => s.id === 's1')?.title).toBe('Renamed');
    expect(steps.find((s) => s.id === 's2')?.body).toBe('Some **markdown**');
  });

  it('maps every step back to the raw events that produced it', () => {
    const { steps, sources } = compile([click('a'), typed('q', 'x'), click('b')]);
    expect(steps).toHaveLength(3);
    expect(Object.values(sources).flat().sort()).toEqual([0, 1, 2]);
  });

  it('lints a step whose element has no durable anchor', () => {
    const positional = click('ghost', {
      context: {
        selectors: [{ kind: 'css', value: 'div > div:nth-of-type(3)', score: 0.6 }],
        fingerprint: {
          elementHash: 'h-ghost',
          stableHash: 's-ghost',
          tagPath: 'html/body/div/div',
          attrs: {},
          neighborText: [],
        },
      },
    });
    const { warnings } = compile([positional]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]?.reason).toMatch(/position/i);
  });
});

describe('sandbox replicas', () => {
  it('attaches a captured replica to the step its event produced', () => {
    const { steps } = compile([
      click('a', { sandboxKey: 'public/w/a.json' }),
      click('b', { sandboxKey: 'public/w/b.json' }),
    ]);
    expect(steps.map((s) => s.sandbox_key)).toEqual(['public/w/a.json', 'public/w/b.json']);
  });

  it('leaves sandbox_key unset when nothing was captured', () => {
    const { steps } = compile([click('a')]);
    expect(steps[0]?.sandbox_key ?? null).toBeNull();
  });

  it('carries the replica through a coalesced typing burst', () => {
    // Only the first keystroke of a burst carries a replica; the fill step it
    // compiles into must still find it via its own source events.
    const { steps } = compile([
      { ...typed('email', 'a'), sandboxKey: 'public/w/form.json' } as RawEvent,
      typed('email', 'ada@example.com'),
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.sandbox_key).toBe('public/w/form.json');
  });

  it('survives a hover fold, inheriting the folded step’s replica', () => {
    // hover then click the SAME element: the hover collapses into the click,
    // handing over its sources — and with them its replica.
    const { steps } = compile([
      { ...hover('save'), sandboxKey: 'public/w/hover.json' } as RawEvent,
      click('save'),
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]?.sandbox_key).toBe('public/w/hover.json');
  });
});

describe('elementKey', () => {
  it('prefers the fingerprint hash, then the primary selector', () => {
    expect(elementKey(target('x'))).toBe('h-x');
    expect(elementKey({ selectors: [{ kind: 'css', value: '#y', score: 1 }] })).toBe('#y');
    expect(elementKey(null)).toBe('');
  });
});
