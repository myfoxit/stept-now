import { beforeEach, describe, expect, it } from 'vitest';
import { buildTarget } from './capture';
import { elementTextHint, serializeSelector, simpleProjection, textHintOf } from './projection';
import type { Target } from './types';

beforeEach(() => {
  document.body.innerHTML = `
    <main>
      <section aria-label="Documents">
        <h2>Your documents</h2>
        <button id="new-doc" data-testid="create-doc">New document</button>
      </section>
    </main>`;
});

describe('simpleProjection', () => {
  it('projects a captured target onto the three backend step fields', () => {
    const target = buildTarget(document.getElementById('new-doc')!);
    const p = simpleProjection(target);

    expect(p.selector).toBe('[data-testid="create-doc"]'); // best css-kind selector
    expect(document.querySelectorAll(p.selector)).toHaveLength(1); // a plain player can use it
    expect(p.fallback_selectors.length).toBeGreaterThan(0);
    expect(p.fallback_selectors.length).toBeLessThanOrEqual(5);
    expect(p.fallback_selectors).not.toContain(p.selector);
    expect(p.fallback_selectors).toContain('aria/New document[role="button"]');
    expect(p.text_hint).toBe('New document');
    // xpath/pierce are cascade-only — they stay in the rich target
    expect(p.fallback_selectors.some((s) => s.startsWith('xpath/'))).toBe(false);
    expect(target.selectors.some((s) => s.kind === 'xpath')).toBe(true);
  });

  it('falls back to a prefixed non-css selector when the target has none', () => {
    const target: Target = {
      selectors: [
        { kind: 'aria', value: 'aria/Save[role="button"]', score: 0.85 },
        { kind: 'text', value: 'text/Save', score: 0.7 },
        { kind: 'xpath', value: 'xpath//html/body/button', score: 0.3 },
      ],
      text: { content: 'Save', exact: true },
    };
    const p = simpleProjection(target);
    expect(p.selector).toBe('aria/Save[role="button"]');
    expect(p.fallback_selectors).toEqual(['text/Save']);
    expect(p.text_hint).toBe('Save');
  });

  it('orders by score, dedups and caps fallbacks at 5', () => {
    const sel = (value: string, score: number) => ({ kind: 'css' as const, value, score });
    const target: Target = {
      selectors: [
        sel('#a', 0.9),
        sel('#dup', 0.5),
        sel('.b', 0.8),
        sel('.c', 0.75),
        sel('.d', 0.7),
        sel('.e', 0.65),
        sel('.f', 0.6),
        sel('#dup', 0.55),
        sel('.g', 0.4),
      ],
    };
    const p = simpleProjection(target);
    expect(p.selector).toBe('#a');
    expect(p.fallback_selectors).toEqual(['.b', '.c', '.d', '.e', '.f']);
    expect(p.text_hint).toBe('');
  });

  it('handles an empty/degenerate target without throwing', () => {
    expect(simpleProjection({ selectors: [] })).toEqual({ selector: '', fallback_selectors: [], text_hint: '' });
    expect(simpleProjection({} as Target).selector).toBe('');
  });

  it('serializeSelector adds the kind prefix once, and never to css', () => {
    expect(serializeSelector({ kind: 'css', value: '#a', score: 1 })).toBe('#a');
    expect(serializeSelector({ kind: 'text', value: 'Save', score: 1 })).toBe('text/Save');
    expect(serializeSelector({ kind: 'text', value: 'text/Save', score: 1 })).toBe('text/Save');
    expect(serializeSelector({ kind: 'pierce', value: '.inner', score: 1 })).toBe('pierce/.inner');
  });
});

describe('text hints', () => {
  it('caps the hint at 80 characters', () => {
    const long = 'x'.repeat(200);
    const hint = textHintOf({ selectors: [], text: { content: long } });
    expect(hint.length).toBe(80);
    expect(hint.endsWith('…')).toBe(true);
  });

  it('falls back to the accessible name, then the fingerprint name', () => {
    expect(textHintOf({ selectors: [], aria: { name: 'Save' } })).toBe('Save');
    expect(
      textHintOf({
        selectors: [],
        fingerprint: { elementHash: 'a', stableHash: 'b', tagPath: 'button', attrs: {}, axName: 'Fp name', neighborText: [] },
      }),
    ).toBe('Fp name');
    expect(textHintOf({ selectors: [] })).toBe('');
  });

  it('elementTextHint prefers aria-label, then text, then title/placeholder/value', () => {
    const el = (html: string) => {
      document.body.innerHTML = html;
      return document.body.firstElementChild!;
    };
    expect(elementTextHint(el('<button aria-label="Delete item">  x  </button>'))).toBe('Delete item');
    expect(elementTextHint(el('<button>  Save\n  changes </button>'))).toBe('Save changes');
    expect(elementTextHint(el('<button title="Tooltip"></button>'))).toBe('Tooltip');
    expect(elementTextHint(el('<input placeholder="Email address" />'))).toBe('Email address');
    expect(elementTextHint(el('<button></button>'))).toBe('');
  });

  it('elementTextHint truncates with an ellipsis at the limit', () => {
    document.body.innerHTML = `<button>${'a'.repeat(120)}</button>`;
    const hint = elementTextHint(document.querySelector('button')!);
    expect(hint.length).toBe(80);
    expect(hint.endsWith('…')).toBe(true);
    expect(elementTextHint(document.querySelector('button')!, 10)).toBe('aaaaaaaaa…');
  });
});
