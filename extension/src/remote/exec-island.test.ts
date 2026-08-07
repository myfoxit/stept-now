/**
 * The remote-exec island's op handler in jsdom: indexing + stamping, index
 * resolution + measurement, find, native-setter value writes (React-style
 * controlled inputs), the password fence, and extraction. `handleExecOp` is
 * chrome-free by design so these run without a browser.
 *
 * Lives OUTSIDE `src/entrypoints/` — WXT registers every file there as an
 * entrypoint, and a co-located test would collide with the island itself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { INDEX_ATTR } from '@stept/dom-capture';
import { handleExecOp } from '../entrypoints/exec.content';

function mount(html: string): void {
  document.body.innerHTML = html;
}

/** jsdom reports every rect as 0×0; give the listed elements a real box so the
 * zero-size filter (which drops un-clickable twins) doesn't eat them. */
function withLayout(selector = 'button,a,input,select,textarea,[role],[contenteditable]'): void {
  let top = 10;
  for (const el of document.querySelectorAll(selector)) {
    const y = top;
    el.getBoundingClientRect = () =>
      ({ top: y, left: 10, bottom: y + 30, right: 120, width: 110, height: 30, x: 10, y }) as DOMRect;
    top += 40;
  }
}

/** Install a fake elementFromPoint (jsdom has none) for occlusion/hit tests. */
function stubElementFromPoint(fn: (x: number, y: number) => Element | null): void {
  (document as unknown as { elementFromPoint: typeof fn }).elementFromPoint = fn;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

afterEach(() => {
  delete (document as unknown as { elementFromPoint?: unknown }).elementFromPoint;
  vi.restoreAllMocks();
});

describe('compact-dom', () => {
  it('indexes, stamps and serializes the interactive elements', async () => {
    mount('<button>Save</button><input aria-label="Email" value="a@b.co">');
    withLayout();
    const r = (await handleExecOp('compact-dom', {})) as { text: string; count: number };
    expect(r.count).toBe(2);
    expect(r.text).toContain('[0]<button');
    expect(r.text).toContain('Save');
    expect(r.text).toContain('value="a@b.co"');
    expect(document.querySelector(`[${INDEX_ATTR}="0"]`)?.tagName).toBe('BUTTON');
    expect(document.querySelector(`[${INDEX_ATTR}="1"]`)?.tagName).toBe('INPUT');
  });

  it('never offers up the extension’s own injected UI or opted-out regions', async () => {
    mount(
      '<div data-stept-drive><button>Ghost ring</button></div>' +
        '<div data-stept-no-ai><button>Fenced</button></div>' +
        '<button>Real</button>',
    );
    withLayout();
    const r = (await handleExecOp('compact-dom', {})) as { text: string; count: number };
    expect(r.count).toBe(1);
    expect(r.text).toContain('Real');
    expect(r.text).not.toContain('Ghost');
    expect(r.text).not.toContain('Fenced');
  });

  it('pages through the listing via offset while keeping original indices', async () => {
    mount('<button>Alpha</button><button>Beta</button><button>Gamma</button>');
    withLayout();
    const page = (await handleExecOp('compact-dom', { offset: 1 })) as { text: string; count: number };
    expect(page.count).toBe(3); // count is the FULL index, not the page
    expect(page.text.startsWith('[1]<button')).toBe(true);
    expect(page.text).not.toContain('[0]');
  });
});

describe('resolve-index', () => {
  it('returns the viewport centre, tag and editability of a stamped element', async () => {
    mount('<button>Go</button><input aria-label="Name">');
    withLayout();
    await handleExecOp('compact-dom', {});
    const button = (await handleExecOp('resolve-index', { index: 0 })) as Record<string, unknown>;
    expect(button).toMatchObject({ found: true, x: 65, y: 25, tag: 'button', editable: false, occluded: false });
    const input = (await handleExecOp('resolve-index', { index: 1 })) as Record<string, unknown>;
    expect(input).toMatchObject({ found: true, tag: 'input', editable: true, contentEditable: false });
  });

  it('reports found:false for an index nothing is stamped with', async () => {
    expect(await handleExecOp('resolve-index', { index: 99 })).toEqual({ found: false });
  });

  it('flags the click point as occluded when an unrelated overlay covers it', async () => {
    mount('<button>Under</button><div id="overlay">spinner</div>');
    withLayout();
    await handleExecOp('compact-dom', {});
    stubElementFromPoint(() => document.getElementById('overlay'));
    const r = (await handleExecOp('resolve-index', { index: 0 })) as { occluded: boolean };
    expect(r.occluded).toBe(true);
  });
});

describe('find', () => {
  it('finds elements by text with tag and visibility', async () => {
    mount('<button>Save draft</button><button>Publish</button>');
    withLayout();
    const hits = (await handleExecOp('find', { query: 'publish' })) as Array<Record<string, unknown>>;
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatchObject({ text: 'Publish', tag: 'button', visible: true });
  });

  it('reports the STAMPED index from the last snapshot, so hits stay actable', async () => {
    mount('<button id="a">Save draft</button><button id="b">Publish</button>');
    withLayout();
    await handleExecOp('compact-dom', {}); // stamps 0 and 1
    document.getElementById('a')!.remove(); // page re-rendered: Publish is now position 0…
    const hits = (await handleExecOp('find', { query: 'Publish' })) as Array<{ index: number }>;
    expect(hits[0]!.index).toBe(1); // …but its ADDRESS is still the stamp resolve-index uses
  });

  it('returns an empty list for a blank query', async () => {
    expect(await handleExecOp('find', { query: '  ' })).toEqual([]);
  });
});

describe('set-value', () => {
  it('writes through the native setter so a React-style value interceptor cannot swallow it', async () => {
    mount('<input id="controlled">');
    withLayout();
    await handleExecOp('compact-dom', {});
    const input = document.getElementById('controlled') as HTMLInputElement;
    const nativeGet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.get!;
    let interceptedWrites = 0;
    // Simulate React's controlled-input tracker: an instance-level `value`
    // property that swallows naive assignments (`input.value = x` would no-op).
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => nativeGet.call(input) as string,
      set: () => {
        interceptedWrites += 1;
      },
    });
    const events: string[] = [];
    input.addEventListener('input', () => events.push('input'));
    input.addEventListener('change', () => events.push('change'));

    await handleExecOp('set-value', { index: 0, value: 'ada@example.com' });

    expect(nativeGet.call(input)).toBe('ada@example.com'); // the DOM really changed
    expect(interceptedWrites).toBe(0); // …without ever touching the interceptor
    expect(events).toEqual(['input', 'change']); // …and the framework was told
  });

  it('sets a contenteditable’s text and fires input', async () => {
    mount('<div id="editor" contenteditable="true" role="textbox">old</div>');
    withLayout();
    const editor = document.getElementById('editor')!;
    // jsdom never reports isContentEditable — pin the flag the real DOM would set
    Object.defineProperty(editor, 'isContentEditable', { value: true, configurable: true });
    await handleExecOp('compact-dom', {});
    const events: string[] = [];
    editor.addEventListener('input', () => events.push('input'));
    await handleExecOp('set-value', { index: 0, value: 'release notes' });
    expect(editor.textContent).toBe('release notes');
    expect(events).toEqual(['input']);
  });

  it('REFUSES to touch a password field', async () => {
    mount('<input type="password" aria-label="Password">');
    withLayout();
    await handleExecOp('compact-dom', {});
    await expect(handleExecOp('set-value', { index: 0, value: 'hunter2' })).rejects.toThrow(
      'refusing to touch a password field',
    );
  });

  it('rejects an element that is not editable', async () => {
    mount('<button>Nope</button>');
    withLayout();
    await handleExecOp('compact-dom', {});
    await expect(handleExecOp('set-value', { index: 0, value: 'x' })).rejects.toThrow('not editable');
  });

  it('throws a fresh-snapshot hint for an unknown index', async () => {
    await expect(handleExecOp('set-value', { index: 3, value: 'x' })).rejects.toThrow('take a fresh snapshot');
  });
});

describe('select / set-checked / select-all', () => {
  it('selects an option by value or visible label and fires change', async () => {
    mount('<select><option value="f">Free</option><option value="p">Pro</option></select>');
    withLayout();
    await handleExecOp('compact-dom', {});
    const select = document.querySelector('select')!;
    const events: string[] = [];
    select.addEventListener('change', () => events.push('change'));
    await handleExecOp('select', { index: 0, text: 'Pro' }); // by label
    expect(select.value).toBe('p');
    await handleExecOp('select', { index: 0, value: 'f' }); // by value
    expect(select.value).toBe('f');
    expect(events).toEqual(['change', 'change']);
    await expect(handleExecOp('select', { index: 0, text: 'Enterprise' })).rejects.toThrow('no option');
  });

  it('toggles a checkbox via click only when the state differs', async () => {
    mount('<input type="checkbox" aria-label="Terms">');
    withLayout();
    await handleExecOp('compact-dom', {});
    const box = document.querySelector('input')!;
    await handleExecOp('set-checked', { index: 0, checked: true });
    expect(box.checked).toBe(true);
    const click = vi.spyOn(box, 'click');
    await handleExecOp('set-checked', { index: 0, checked: true }); // already true → no click
    expect(click).not.toHaveBeenCalled();
  });

  it('select-all selects the field’s whole current value', async () => {
    mount('<input value="Untitled document">');
    withLayout();
    await handleExecOp('compact-dom', {});
    const input = document.querySelector('input')!;
    await handleExecOp('select-all', { index: 0 });
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('Untitled document'.length);
  });
});

describe('extract', () => {
  it('reads element text, form values, attributes and the page url', async () => {
    mount('<button data-id="b-7">Total: 42</button><input aria-label="Code" value=" otp-123 ">');
    withLayout();
    await handleExecOp('compact-dom', {});
    expect(await handleExecOp('extract', { index: 0, kind: 'text' })).toEqual({ kind: 'text', value: 'Total: 42' });
    expect(await handleExecOp('extract', { index: 1, kind: 'text' })).toEqual({ kind: 'text', value: 'otp-123' });
    expect(await handleExecOp('extract', { index: 0, kind: 'attr', attr: 'data-id' })).toEqual({
      kind: 'attr',
      value: 'b-7',
    });
    const url = (await handleExecOp('extract', { kind: 'url' })) as { kind: string; value: string };
    expect(url.kind).toBe('url');
    expect(url.value).toBe(location.href);
  });

  it('REFUSES to read a password field — including its attributes', async () => {
    mount('<input type="password" value="hunter2" aria-label="Password">');
    withLayout();
    await handleExecOp('compact-dom', {});
    await expect(handleExecOp('extract', { index: 0, kind: 'text' })).rejects.toThrow(
      'refusing to touch a password field',
    );
    await expect(handleExecOp('extract', { index: 0, kind: 'attr', attr: 'value' })).rejects.toThrow(
      'refusing to touch a password field',
    );
  });
});

describe('page-text / hit-test / url / dom-settle', () => {
  it('page-text reads visible prose but never the extension’s own chrome', async () => {
    mount('<main><h1>Billing</h1><p>Invoices are sent monthly.</p></main><div data-stept-guide>Step 2 of 5</div>');
    const text = (await handleExecOp('page-text', {})) as string;
    expect(text).toContain('Billing');
    expect(text).toContain('Invoices are sent monthly.');
    expect(text).not.toContain('Step 2 of 5');
  });

  it('hit-test describes what a coordinate lands on, with the stamped idx', async () => {
    mount('<button aria-label="Delete"><span id="glyph">×</span></button>');
    withLayout();
    await handleExecOp('compact-dom', {});
    stubElementFromPoint(() => document.getElementById('glyph'));
    const hit = (await handleExecOp('hit-test', { x: 20, y: 20 })) as Record<string, unknown>;
    expect(hit).toMatchObject({ tag: 'span', text: 'Delete', idx: 0 });
  });

  it('hit-test returns null when the point hits nothing', async () => {
    stubElementFromPoint(() => null);
    expect(await handleExecOp('hit-test', { x: 5, y: 5 })).toBeNull();
  });

  it('url reports href + title; dom-settle resolves on a quiet page', async () => {
    document.title = 'Stept — Billing';
    const r = (await handleExecOp('url', {})) as { url: string; title: string };
    expect(r.url).toBe(location.href);
    expect(r.title).toBe('Stept — Billing');
    await expect(handleExecOp('dom-settle', { ms: 50 })).resolves.toBe(true);
  });
});
