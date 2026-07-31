import { beforeEach, describe, expect, it } from 'vitest';
import { minimalTarget, parseSelector, resolveStepTarget } from './resolve-step';

/** The bridge between a stored step and a live element. A hand-authored step
 * (dashboard: selector + fallbacks + text hint) must run through the SAME
 * cascade as a recorded one — that is the whole point of lifting it into a
 * minimal Target. */

beforeEach(() => {
  document.body.innerHTML = `
    <nav><button id="primary" aria-label="Save changes">Save</button></nav>
    <main><a href="/x" data-testid="go">Continue</a></main>
  `;
});

describe('parseSelector', () => {
  it('splits DevTools-Recorder prefixes off, and leaves css alone', () => {
    expect(parseSelector('#id', 0.9)).toEqual({ kind: 'css', value: '#id', score: 0.9 });
    expect(parseSelector('aria/Save changes', 0.8)).toEqual({
      kind: 'aria',
      value: 'Save changes',
      score: 0.8,
    });
    expect(parseSelector('text/Continue', 0.7)).toEqual({
      kind: 'text',
      value: 'Continue',
      score: 0.7,
    });
    expect(parseSelector('xpath//html/body', 0.3).kind).toBe('xpath');
  });
});

describe('minimalTarget', () => {
  it('orders primary → fallbacks → text hint by descending score', () => {
    const t = minimalTarget('#primary', ['aria/Save changes'], 'Save');
    expect(t.selectors.map((s) => [s.kind, s.value])).toEqual([
      ['css', '#primary'],
      ['aria', 'Save changes'],
      ['text', 'Save'],
    ]);
    expect(t.selectors[0]!.score).toBeGreaterThan(t.selectors[1]!.score);
    expect(t.selectors[1]!.score).toBeGreaterThan(t.selectors[2]!.score);
    expect(t.text?.content).toBe('Save');
  });
});

describe('resolveStepTarget', () => {
  it('resolves a hand-authored step from its primary selector alone', () => {
    const r = resolveStepTarget(document, { selector: '#primary' });
    expect(r.element?.id).toBe('primary');
    expect(r.healed).toBe(false);
  });

  it('heals to a fallback when the primary is gone', () => {
    const r = resolveStepTarget(document, {
      selector: '#gone',
      fallbackSelectors: ['[data-testid="go"]'],
    });
    expect((r.element as HTMLElement | null)?.dataset.testid).toBe('go');
    expect(r.healed).toBe(true);
  });

  it('falls back to the text hint as the last resort', () => {
    const r = resolveStepTarget(document, { selector: '#gone', textHint: 'Continue' });
    expect(r.element?.textContent).toBe('Continue');
    expect(r.healed).toBe(true);
  });

  it('reports a miss instead of guessing', () => {
    const r = resolveStepTarget(document, { selector: '#nope', textHint: 'nothing like this' });
    expect(r.element).toBeNull();
  });

  it('prefers the rich target when the step carries one', () => {
    const r = resolveStepTarget(document, {
      target: { selectors: [{ kind: 'css', value: '#primary', score: 0.95 }] },
      selector: '#gone',
    });
    expect(r.element?.id).toBe('primary');
  });
});
