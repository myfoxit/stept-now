import { afterEach, describe, expect, it } from 'vitest';

import {
  cssAttrValue,
  cssEscape,
  elementTextHint,
  generateSelector,
} from './selector';

afterEach(() => {
  document.body.innerHTML = '';
});

function mount(html: string): void {
  document.body.innerHTML = html;
}

describe('cssEscape', () => {
  it('escapes CSS meta characters deterministically', () => {
    expect(cssEscape('foo.bar')).toBe('foo\\.bar');
    expect(cssEscape('a:b')).toBe('a\\:b');
  });

  it('escapes a leading digit per the CSSOM algorithm', () => {
    expect(cssEscape('1col')).toBe('\\31 col');
  });
});

describe('cssAttrValue', () => {
  it('escapes quotes and backslashes for a quoted attribute value', () => {
    expect(cssAttrValue('say "hi"')).toBe('say \\"hi\\"');
    expect(cssAttrValue('a\\b')).toBe('a\\\\b');
  });
});

describe('generateSelector', () => {
  it('prefers [data-tour] over id and structure', () => {
    mount('<div id="wrap"><button data-tour="save" id="save-btn">Save</button></div>');
    const el = document.querySelector('[data-tour="save"]')!;
    const sel = generateSelector(el);
    expect(sel).toBe('[data-tour="save"]');
    expect(document.querySelectorAll(sel)).toHaveLength(1);
  });

  it('falls back past a non-unique data-tour to a unique selector', () => {
    mount(
      '<ul><li data-tour="row">a</li><li data-tour="row">b</li></ul>',
    );
    const second = document.querySelectorAll('[data-tour="row"]')[1]!;
    const sel = generateSelector(second);
    // data-tour is ambiguous, so it must not be used alone.
    expect(sel).not.toBe('[data-tour="row"]');
    expect(document.querySelectorAll(sel)).toHaveLength(1);
    expect(document.querySelector(sel)).toBe(second);
  });

  it('uses #id when there is no data-tour', () => {
    mount('<section><span id="hero">Hi</span></section>');
    const el = document.getElementById('hero')!;
    expect(generateSelector(el)).toBe('#hero');
  });

  it('escapes special characters in an id and still matches uniquely', () => {
    mount('<div id="nav.item">x</div>');
    const el = document.querySelector('div')!;
    const sel = generateSelector(el);
    expect(sel).toContain('\\'); // escaping happened
    expect(sel).toBe('#nav\\.item');
    expect(document.querySelectorAll(sel)).toHaveLength(1);
    expect(document.querySelector(sel)).toBe(el);
  });

  it('escapes a quote inside a data-tour value', () => {
    mount('<button data-tour=\'say "hi"\'>x</button>');
    const el = document.querySelector('button')!;
    const sel = generateSelector(el);
    expect(sel).toBe('[data-tour="say \\"hi\\""]');
    expect(document.querySelectorAll(sel)).toHaveLength(1);
  });

  it('disambiguates same-tag siblings with :nth-of-type', () => {
    mount('<ul><li>a</li><li>b</li><li>c</li></ul>');
    const items = Array.from(document.querySelectorAll('li'));
    const middle = items[1]!;
    const sel = generateSelector(middle);
    expect(sel).toContain(':nth-of-type(2)');
    // Every generated selector is unique and points back at its element.
    for (const li of items) {
      const s = generateSelector(li);
      expect(document.querySelectorAll(s)).toHaveLength(1);
      expect(document.querySelector(s)).toBe(li);
    }
  });

  it('omits :nth-of-type when the tag is unique among its siblings', () => {
    mount('<article><h1>Title</h1><p>body</p></article>');
    const h1 = document.querySelector('h1')!;
    const sel = generateSelector(h1);
    expect(sel).not.toContain('nth-of-type');
    expect(document.querySelectorAll(sel)).toHaveLength(1);
  });

  it('anchors on a unique ancestor id when structure alone is ambiguous', () => {
    // Two structurally identical subtrees; only the id on the outer div of the
    // first distinguishes them, so the path must fall back to the #app anchor.
    mount(`
      <div id="app"><div><div><button>Go</button></div></div></div>
      <div><div><div><button>Go</button></div></div></div>
    `);
    const target = document.querySelector('#app button')!;
    const sel = generateSelector(target);
    expect(sel.startsWith('#app')).toBe(true);
    expect(document.querySelectorAll(sel)).toHaveLength(1);
    expect(document.querySelector(sel)).toBe(target);
  });

  it('guarantees uniqueness for deeply nested, id-less structures', () => {
    mount(`
      <main>
        <div><div><span>one</span></div></div>
        <div><div><span>two</span></div></div>
      </main>
    `);
    const spans = Array.from(document.querySelectorAll('span'));
    for (const span of spans) {
      const sel = generateSelector(span);
      expect(document.querySelectorAll(sel)).toHaveLength(1);
      expect(document.querySelector(sel)).toBe(span);
    }
  });
});

describe('elementTextHint', () => {
  it('prefers aria-label, then trims and collapses whitespace', () => {
    mount('<button aria-label="  Save   changes ">ignored</button>');
    expect(elementTextHint(document.querySelector('button')!)).toBe('Save changes');
  });

  it('falls back to text content and truncates long text', () => {
    mount(`<button>${'x'.repeat(200)}</button>`);
    const hint = elementTextHint(document.querySelector('button')!, 10);
    expect(hint.length).toBe(10);
    expect(hint.endsWith('…')).toBe(true);
  });
});
