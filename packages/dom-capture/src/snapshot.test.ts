/**
 * Snapshot capture: what survives into the replica, what must not, and the
 * live state that only exists in memory at capture time.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  absolutizeCss,
  absolutizeSrcset,
  absolutizeUrl,
  captureSnapshot,
  collectStyles,
  renderSnapshot,
  SANDBOX_ATTR,
  snapshotBytes,
  SNAPSHOT_VERSION,
} from './snapshot';

const BASE = 'https://app.example.com/dashboard/';

function mount(html: string): Document {
  document.documentElement.innerHTML = html;
  return document;
}

/**
 * Stub `document.styleSheets` for one test. The whole file shares a single
 * jsdom document, so an un-restored `defineProperty` here silently starves
 * every later test of its stylesheets — restore it rather than trusting order.
 */
let restoreStyleSheets: (() => void) | null = null;

function stubStyleSheets(doc: Document, sheets: unknown[]): void {
  const original = Object.getOwnPropertyDescriptor(Document.prototype, 'styleSheets');
  Object.defineProperty(doc, 'styleSheets', { configurable: true, value: sheets });
  restoreStyleSheets = () => {
    delete (doc as unknown as Record<string, unknown>).styleSheets;
    if (original && !Object.getOwnPropertyDescriptor(doc, 'styleSheets')) return;
    if (original) Object.defineProperty(doc, 'styleSheets', original);
  };
}

/** A sheet whose rules this origin may not read, as the browser presents it. */
function crossOriginSheet(href: string) {
  return {
    href,
    get cssRules(): never {
      throw new DOMException('cross-origin', 'SecurityError');
    },
  };
}

beforeEach(() => {
  document.documentElement.innerHTML = '<head></head><body></body>';
});

afterEach(() => {
  restoreStyleSheets?.();
  restoreStyleSheets = null;
});

// --- URL rewriting ----------------------------------------------------------

describe('absolutizeUrl', () => {
  it('resolves relative URLs against the capture base', () => {
    expect(absolutizeUrl('/logo.png', BASE)).toBe('https://app.example.com/logo.png');
    expect(absolutizeUrl('icons/a.svg', BASE)).toBe(
      'https://app.example.com/dashboard/icons/a.svg',
    );
  });

  it('leaves absolute, opaque and non-navigating values alone', () => {
    for (const value of [
      'https://cdn.example.com/x.png',
      'data:image/png;base64,AAAA',
      'blob:https://app.example.com/abc',
      'mailto:a@b.co',
      'tel:+1555',
      '#anchor',
      '',
    ]) {
      expect(absolutizeUrl(value, BASE)).toBe(value);
    }
  });

  it('erases script-bearing schemes rather than resolving them', () => {
    expect(absolutizeUrl('javascript:alert(1)', BASE)).toBe('');
    expect(absolutizeUrl('  JavaScript:alert(1)', BASE)).toBe('');
    expect(absolutizeUrl('vbscript:msgbox(1)', BASE)).toBe('');
  });

  it('returns the input when it cannot be parsed', () => {
    expect(absolutizeUrl('http://[', BASE)).toBe('http://[');
  });
});

describe('absolutizeSrcset', () => {
  it('rewrites every candidate while keeping its descriptor', () => {
    expect(absolutizeSrcset('a.png 1x, /b.png 2x', BASE)).toBe(
      'https://app.example.com/dashboard/a.png 1x, https://app.example.com/b.png 2x',
    );
  });

  it('handles a bare single candidate and stray commas', () => {
    expect(absolutizeSrcset('a.png', BASE)).toBe('https://app.example.com/dashboard/a.png');
    expect(absolutizeSrcset('a.png ,, ', BASE)).toBe('https://app.example.com/dashboard/a.png');
  });
});

describe('absolutizeCss', () => {
  it('rewrites url() with any quoting style', () => {
    const css = ".a{background:url(bg.png)}.b{background:url('/x.png')}.c{background:url(\"y.png\")}";
    const out = absolutizeCss(css, BASE);
    expect(out).toContain('url(https://app.example.com/dashboard/bg.png)');
    expect(out).toContain("url('https://app.example.com/x.png')");
    expect(out).toContain('url("https://app.example.com/dashboard/y.png")');
  });

  it('leaves data URIs untouched', () => {
    const css = '.a{background:url(data:image/gif;base64,R0lGOD)}';
    expect(absolutizeCss(css, BASE)).toBe(css);
  });
});

// --- what must not survive --------------------------------------------------

describe('captureSnapshot hardening', () => {
  it('drops scripts and inline event handlers', () => {
    const doc = mount(
      '<body><script>window.x=1</script><button onclick="steal()" onmouseover="x()">Go</button></body>',
    );
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toContain('<script');
    expect(snap.html).not.toContain('onclick');
    expect(snap.html).not.toContain('onmouseover');
    expect(snap.html).toContain('Go');
  });

  it('defuses anchors so a click cannot navigate away from the replica', () => {
    const doc = mount('<body><a href="/settings">Settings</a></body>');
    const expected = new URL('/settings', doc.baseURI).href;
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toMatch(/\shref=/);
    expect(snap.html).toContain(`data-stept-href="${expected}"`);
    expect(snap.html).toContain('Settings');
  });

  it('strips form targets', () => {
    const doc = mount('<body><form action="/pay" target="_top"><input name="a"></form></body>');
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toContain('action=');
    expect(snap.html).not.toContain('target=');
  });

  it('replaces nested frames with a same-sized placeholder', () => {
    const doc = mount('<body><iframe src="https://other.example.com/x"></iframe></body>');
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toContain('<iframe');
    expect(snap.html).toContain('data-stept-omitted="iframe"');
    expect(snap.omitted.frames).toBe(1);
  });

  it('masks password and credit-card fields without changing their shape', () => {
    const doc = mount(
      '<body><input type="password" id="p"><input autocomplete="cc-number" id="c"></body>',
    );
    (doc.getElementById('p') as HTMLInputElement).value = 'hunter2';
    (doc.getElementById('c') as HTMLInputElement).value = '4111111111111111';
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toContain('hunter2');
    expect(snap.html).not.toContain('4111111111111111');
    expect(snap.html).toContain('value="•••••••"');
    expect(snap.omitted.masked).toBe(2);
  });

  it('honours caller-supplied mask selectors on leaf elements only', () => {
    const doc = mount(
      '<body><span class="pii">Ada Lovelace</span><div class="pii"><b>keep</b></div></body>',
    );
    const snap = captureSnapshot(doc, { maskSelectors: ['.pii'] });
    expect(snap.html).not.toContain('Ada Lovelace');
    expect(snap.html).toContain('••••••••••••');
    // A masked container keeps its subtree rather than being blanked.
    expect(snap.html).toContain('<b>keep</b>');
  });

  it('never mutates the page it captured', () => {
    const doc = mount('<body><a href="/x">L</a><script>1</script></body>');
    captureSnapshot(doc);
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/x');
    expect(doc.querySelector('script')).not.toBeNull();
  });
});

// --- live state -------------------------------------------------------------

describe('captureSnapshot live state', () => {
  it('writes in-memory input values back into attributes', () => {
    const doc = mount('<body><input id="t"><textarea id="a"></textarea></body>');
    (doc.getElementById('t') as HTMLInputElement).value = 'typed';
    (doc.getElementById('a') as HTMLTextAreaElement).value = 'notes';
    const snap = captureSnapshot(doc);
    expect(snap.html).toContain('value="typed"');
    expect(snap.html).toContain('>notes</textarea>');
  });

  it('records checkbox and radio state', () => {
    const doc = mount('<body><input type="checkbox" id="c"><input type="radio" id="r"></body>');
    (doc.getElementById('c') as HTMLInputElement).checked = true;
    const snap = captureSnapshot(doc);
    expect(snap.html).toMatch(/type="checkbox"[^>]*checked/);
    expect(snap.html).not.toMatch(/type="radio"[^>]*checked/);
  });

  it('records the selected option', () => {
    const doc = mount('<body><select><option>A</option><option>B</option></select></body>');
    (doc.querySelector('select') as HTMLSelectElement).selectedIndex = 1;
    const snap = captureSnapshot(doc);
    expect(snap.html).toMatch(/<option selected="">B<\/option>|<option selected>B<\/option>/);
  });

  it('stashes container scroll offsets the DOM would otherwise lose', () => {
    const doc = mount('<body><div id="s">x</div></body>');
    const scroller = doc.getElementById('s') as HTMLElement;
    Object.defineProperty(scroller, 'scrollTop', { value: 240, configurable: true });
    Object.defineProperty(scroller, 'scrollLeft', { value: 10, configurable: true });
    const snap = captureSnapshot(doc);
    expect(snap.html).toContain('data-stept-scroll="10,240"');
  });

  it('reports the viewport, title and document scroll', () => {
    const doc = mount('<head><title>Billing</title></head><body>x</body>');
    const snap = captureSnapshot(doc);
    expect(snap.version).toBe(SNAPSHOT_VERSION);
    expect(snap.title).toBe('Billing');
    expect(snap.viewport.w).toBeGreaterThan(0);
    expect(snap.scroll).toEqual({ x: 0, y: 0 });
  });
});

// --- stylesheets ------------------------------------------------------------

describe('collectStyles', () => {
  it('inlines readable rules and absolutizes their urls', () => {
    const doc = mount('<head><style>.a{background:url(bg.png)}</style></head><body></body>');
    const { css, blocked } = collectStyles(doc, BASE);
    expect(css.join('')).toContain('https://app.example.com/dashboard/bg.png');
    expect(blocked).toEqual([]);
  });

  it('reports sheets whose rules this origin may not read', () => {
    const doc = mount('<head></head><body></body>');
    const href = 'https://cdn.other.com/app.css';
    stubStyleSheets(doc, [crossOriginSheet(href)]);
    const { css, blocked } = collectStyles(doc, BASE);
    expect(css).toEqual([]);
    expect(blocked).toEqual([href]);
  });
});

describe('captureSnapshot stylesheets', () => {
  it('lifts inline <style> into the envelope so it is not applied twice', () => {
    const doc = mount('<head><style>.a{color:red}</style></head><body><i class="a">x</i></body>');
    const snap = captureSnapshot(doc);
    expect(snap.css.join('')).toContain('color: red');
    expect(snap.html).not.toContain('<style');
  });

  it('keeps the <link> for a sheet it could not read', () => {
    const doc = mount(
      '<head><link rel="stylesheet" href="https://cdn.other.com/app.css"></head><body></body>',
    );
    stubStyleSheets(doc, [crossOriginSheet('https://cdn.other.com/app.css')]);
    const snap = captureSnapshot(doc);
    expect(snap.blockedStyles).toEqual(['https://cdn.other.com/app.css']);
    expect(snap.html).toContain('https://cdn.other.com/app.css');
  });

  it('drops preload hints that only cost bandwidth in a replica', () => {
    const doc = mount(
      '<head><link rel="preload" as="font" href="/f.woff2"><link rel="modulepreload" href="/m.js"></head><body></body>',
    );
    const snap = captureSnapshot(doc);
    expect(snap.html).not.toContain('preload');
  });
});

// --- rendering --------------------------------------------------------------

describe('renderSnapshot', () => {
  const snap = () => captureSnapshot(mount('<head><title>T</title></head><body><p>hi</p></body>'));

  it('emits one self-contained document', () => {
    const out = renderSnapshot(snap());
    expect(out.startsWith('<!doctype html>')).toBe(true);
    expect(out).toContain('<meta charset="utf-8">');
    expect(out).toContain('<p>hi</p>');
  });

  it('pins a base href so anything unrewritten still resolves', () => {
    const out = renderSnapshot({ ...snap(), url: BASE });
    expect(out).toContain(`<base href="${BASE}">`);
  });

  it('carries a script-blocking CSP as defence in depth', () => {
    const out = renderSnapshot(snap());
    expect(out).toContain("script-src 'none'");
    expect(out).toContain("form-action 'none'");
  });

  it('escapes a hostile title and url instead of letting them break out', () => {
    const out = renderSnapshot({
      ...snap(),
      title: '</title><script>alert(1)</script>',
      url: '"><script>alert(1)</script>',
    });
    expect(out).not.toContain('<script>alert(1)</script>');
    expect(out).toContain('&lt;script&gt;');
  });

  it('appends caller CSS last so the player can style its own overlay', () => {
    const out = renderSnapshot(snap(), { extraCss: '.stept-hit{outline:2px solid red}' });
    expect(out).toContain('.stept-hit{outline:2px solid red}');
  });

  it('locks the host iframe down by exporting an empty sandbox', () => {
    expect(SANDBOX_ATTR).toBe('');
  });
});

describe('snapshotBytes', () => {
  it('measures the serialized envelope', () => {
    const s = captureSnapshot(mount('<body>hello</body>'));
    expect(snapshotBytes(s)).toBe(new TextEncoder().encode(JSON.stringify(s)).length);
  });
});
