/**
 * The compact interactive view: what the driving AI sees, in what order, and
 * which live state has to travel with it.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import {
  findByText,
  formState,
  INDEX_ATTR,
  indexInteractive,
  orderSnapshotElements,
  pageText,
  serializeCompact,
  stampIndex,
  topmostOverlay,
  type IndexedElement,
} from './compact';

function mount(html: string): Document {
  document.body.innerHTML = html;
  return document;
}

/** jsdom reports every rect as 0×0; give the listed elements a real box so the
 * zero-size filter (which exists to drop un-clickable twins) doesn't eat them. */
function withLayout(doc: Document, selector = 'button,a,input,select,textarea,[role]'): void {
  for (const el of doc.querySelectorAll(selector)) {
    el.getBoundingClientRect = () =>
      ({ top: 10, left: 10, bottom: 40, right: 120, width: 110, height: 30, x: 10, y: 10 }) as DOMRect;
  }
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('formState', () => {
  it('reports a text input value but masks a password', () => {
    const doc = mount('<input id="a" value="ada@example.com"><input id="b" type="password" value="hunter2">');
    expect(formState(doc.getElementById('a')!).value).toBe('ada@example.com');
    expect(formState(doc.getElementById('b')!).value).toBe('•••');
  });

  it('omits the value of an empty password so nothing hints at a secret', () => {
    const doc = mount('<input id="b" type="password" value="">');
    expect(formState(doc.getElementById('b')!).value).toBeUndefined();
  });

  it('reports checkbox/radio state as checked, not value', () => {
    const doc = mount('<input id="c" type="checkbox" checked>');
    const state = formState(doc.getElementById('c')!);
    expect(state.checked).toBe(true);
    expect(state.value).toBeUndefined();
  });

  it('falls back to aria-checked and reads aria-disabled/expanded', () => {
    const doc = mount(
      '<div id="s" role="switch" aria-checked="true" aria-disabled="true"></div>' +
        '<button id="m" aria-expanded="false">Menu</button>',
    );
    expect(formState(doc.getElementById('s')!)).toMatchObject({ checked: true, disabled: true });
    expect(formState(doc.getElementById('m')!).expanded).toBe(false);
  });

  it('reads the selected option label of a select', () => {
    const doc = mount('<select id="p"><option>Free</option><option selected>Pro</option></select>');
    expect(formState(doc.getElementById('p')!).value).toBe('Pro');
  });

  it('caps long values so live state cannot blow the char budget', () => {
    const doc = mount(`<textarea id="t">${'x'.repeat(200)}</textarea>`);
    expect(formState(doc.getElementById('t')!).value).toHaveLength(40);
  });
});

describe('ordering', () => {
  const ref = (key: string, z: number, order: number) => ({ key, z, order });

  it('picks the highest z-index overlay, then latest document order', () => {
    expect(
      topmostOverlay([{ overlay: ref('a', 10, 0) }, { overlay: ref('b', 50, 1) }])?.key,
    ).toBe('b');
    expect(
      topmostOverlay([{ overlay: ref('a', 10, 0) }, { overlay: ref('b', 10, 3) }])?.key,
    ).toBe('b');
    expect(topmostOverlay([{ overlay: null }])).toBeNull();
  });

  it('puts the topmost overlay first, then in-viewport, then the rest', () => {
    const ordered = orderSnapshotElements([
      { id: 'below', inViewport: false },
      { id: 'onscreen', inViewport: true },
      { id: 'modal', overlay: ref('m', 100, 0), inViewport: true },
    ] as Array<{ id: string; inViewport?: boolean; overlay?: ReturnType<typeof ref> | null }>);
    expect(ordered.map((entry) => entry.id)).toEqual(['modal', 'onscreen', 'below']);
  });

  it('leaves an ordinary page untouched', () => {
    const input = [{ id: 'a', inViewport: true }, { id: 'b', inViewport: true }]
    expect(orderSnapshotElements(input).map((e) => e.id)).toEqual(['a', 'b']);
  });
});

describe('indexInteractive', () => {
  it('indexes interactive elements with role, name and live state', () => {
    const doc = mount('<button>Save</button><input aria-label="Email" value="a@b.co">');
    withLayout(doc);
    const index = indexInteractive(doc);
    expect(index.map((entry) => entry.name)).toEqual(['Save', 'Email']);
    expect(index[1]!.value).toBe('a@b.co');
  });

  it('skips excluded elements — the widget must not offer up its own UI', () => {
    const doc = mount('<div id="stept-frame"><button>Widget close</button></div><button>Real</button>');
    withLayout(doc);
    const index = indexInteractive(doc, {
      exclude: (el) => Boolean(el.closest('#stept-frame')),
    });
    expect(index.map((entry) => entry.name)).toEqual(['Real']);
  });

  it('drops zero-size candidates but keeps options of a closed select', () => {
    const doc = mount('<button id="ghost">Ghost</button><select><option>One</option></select>');
    withLayout(doc, 'select');
    const index = indexInteractive(doc);
    expect(index.some((entry) => entry.name === 'Ghost')).toBe(false);
    expect(index.some((entry) => entry.tag === 'option')).toBe(true);
  });

  it('caps the index and stamps addressable attributes', () => {
    const doc = mount('<button>A</button><button>B</button><button>C</button>');
    withLayout(doc);
    const index = indexInteractive(doc, { cap: 2 });
    expect(index).toHaveLength(2);
    stampIndex(doc, index);
    expect(doc.querySelector(`[${INDEX_ATTR}="1"]`)?.textContent).toBe('B');
  });

  it('clears stale stamps so a re-render cannot leave a duplicate index behind', () => {
    const doc = mount('<button>A</button><button>B</button>');
    withLayout(doc);
    stampIndex(doc, indexInteractive(doc));
    doc.body.innerHTML = '<button>Only</button>';
    withLayout(doc);
    stampIndex(doc, indexInteractive(doc));
    expect(doc.querySelectorAll(`[${INDEX_ATTR}]`)).toHaveLength(1);
  });
});

describe('serializeCompact', () => {
  const entry = (over: Partial<IndexedElement> & { index: number }): IndexedElement => ({
    el: document.createElement('button'),
    tag: 'button',
    role: 'button',
    name: '',
    text: '',
    ...over,
  });

  it('renders index, tag, role, name and text', () => {
    expect(serializeCompact([entry({ index: 0, name: 'Save', text: 'Save changes' })])).toBe(
      '[0]<button name="Save"> Save changes',
    );
  });

  it('omits text identical to the name instead of repeating it', () => {
    expect(serializeCompact([entry({ index: 0, name: 'Save', text: 'Save' })])).toBe(
      '[0]<button name="Save">',
    );
  });

  it('carries value, checked, disabled and expanded state', () => {
    const line = serializeCompact([
      entry({ index: 4, name: 'Plan', value: 'Pro', checked: false, disabled: true, expanded: true }),
    ]);
    expect(line).toContain('value="Pro"');
    expect(line).toContain('checked=false');
    expect(line).toContain('disabled');
    expect(line).toContain('expanded=true');
  });

  it('includes placeholder and type from the element', () => {
    const el = document.createElement('input');
    el.setAttribute('placeholder', 'you@example.com');
    el.setAttribute('type', 'email');
    const line = serializeCompact([entry({ index: 0, el, tag: 'input', role: 'textbox' })]);
    expect(line).toBe('[0]<input role=textbox placeholder="you@example.com" type=email>');
  });

  it('truncates on the char budget and names the offset to continue from', () => {
    const index = Array.from({ length: 30 }, (_, i) => entry({ index: i, name: `Button ${i}` }));
    const out = serializeCompact(index, 80);
    expect(out).toMatch(/page_snapshot with offset=\d+ to continue/);
    expect(out.split('\n').length).toBeLessThan(30);
  });

  it('keeps ORIGINAL indexes when paging with an offset', () => {
    const index = Array.from({ length: 5 }, (_, i) => entry({ index: i, name: `B${i}` }));
    expect(serializeCompact(index, 8000, 3).split('\n')[0]).toBe('[3]<button name="B3">');
  });

  it('says so when the offset is past the end rather than returning silence', () => {
    expect(serializeCompact([entry({ index: 0 })], 8000, 9)).toContain('past the end');
  });

  it('always emits at least one line, even under an impossible budget', () => {
    expect(serializeCompact([entry({ index: 0, name: 'Save' })], 1)).toContain('[0]<button');
  });
});

describe('findByText', () => {
  const make = (index: number, name: string, text = ''): IndexedElement => {
    const el = document.createElement('button');
    el.getBoundingClientRect = () =>
      ({ top: 0, left: 0, bottom: 20, right: 60, width: 60, height: 20, x: 0, y: 0 }) as DOMRect;
    return { index, el, tag: 'button', role: 'button', name, text };
  };

  it('ranks an exact name above a longer substring match', () => {
    const found = findByText([make(0, 'Save and close'), make(1, 'Save')], 'save');
    expect(found.map((f) => f.index)).toEqual([1, 0]);
  });

  it('matches visible text when the accessible name does not carry it', () => {
    expect(findByText([make(7, '', 'Add invoice')], 'invoice')[0]!.index).toBe(7);
  });

  it('reports whether each match is on screen so the caller knows to scroll', () => {
    const offscreen = make(0, 'Far below');
    offscreen.el.getBoundingClientRect = () =>
      ({ top: 5000, left: 0, bottom: 5020, right: 60, width: 60, height: 20, x: 0, y: 5000 }) as DOMRect;
    expect(findByText([offscreen], 'far')[0]!.visible).toBe(false);
  });

  it('honours the limit and returns nothing for a blank query', () => {
    const index = Array.from({ length: 8 }, (_, i) => make(i, `Save ${i}`));
    expect(findByText(index, 'save', 3)).toHaveLength(3);
    expect(findByText(index, '   ')).toEqual([]);
  });
});

describe('pageText', () => {
  it('collapses runs of spaces but keeps block boundaries as newlines', () => {
    const doc = mount('<p>Hello   world</p><p>Second line</p>');
    expect(pageText(doc)).toBe('Hello world\nSecond line');
    expect(pageText(doc, 5)).toHaveLength(5);
  });

  it('keeps inline text around a nested element instead of dropping it', () => {
    const doc = mount('<p>Total is <b>42</b> today</p>');
    expect(pageText(doc)).toBe('Total is 42 today');
  });

  it('leaves out hidden subtrees and script/style noise', () => {
    const doc = mount(
      '<style>.x{color:red}</style><div hidden>Secret</div><p>Visible</p>' +
        '<div aria-hidden="true">Decorative</div>',
    );
    expect(pageText(doc)).toBe('Visible');
  });

  it('leaves out excluded subtrees so the widget does not read itself back', () => {
    const doc = mount('<div id="stept-frame"><p>Widget chrome</p></div><p>Real content</p>');
    const text = pageText(doc, 8000, (el) => el.id === 'stept-frame');
    expect(text).toContain('Real content');
    expect(text).not.toContain('Widget chrome');
  });
});
