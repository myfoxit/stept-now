/**
 * DOM snapshotting — the capture half of sandbox mode.
 *
 * `captureSnapshot` freezes a live page into a self-contained, *inert* replica:
 * the markup plus whatever stylesheet text is readable from this origin, with
 * live form state written back into attributes so the clone looks like what the
 * recorder actually saw. `renderSnapshot` turns that envelope back into one HTML
 * document suitable for an iframe's `srcdoc`.
 *
 * The replica is deliberately script-free. A sandboxed iframe still gives you
 * everything the user agent does natively — scrolling, hover and focus styles,
 * typing into inputs, opening `<select>`s and `<details>` — which is the bulk of
 * what makes a captured screen feel live, without ever running the captured
 * app's JavaScript on a Stept origin.
 *
 * SECURITY CONTRACT: the string from `renderSnapshot` is untrusted markup from
 * the customer's page. It MUST be mounted in an iframe whose `sandbox` attribute
 * is present and omits BOTH `allow-scripts` and `allow-same-origin`. `SANDBOX_ATTR`
 * is that value; use it rather than writing the tokens out by hand.
 *
 * Like the rest of this package: zero dependencies, no `chrome.*`, nothing
 * touched at module scope, so it runs in the recorder, the dashboard and jsdom.
 */

/** The `sandbox` attribute a host iframe must carry. Empty = maximum lockdown. */
export const SANDBOX_ATTR = '';

/** Envelope version. Bump when `PageSnapshot` changes shape incompatibly. */
export const SNAPSHOT_VERSION = 1;

export interface SnapshotViewport {
  w: number;
  h: number;
}

export interface PageSnapshot {
  version: number;
  /** Serialized `<html>` subtree: scripts stripped, live state inlined. */
  html: string;
  /** Readable same-origin stylesheet text, in document order. */
  css: string[];
  /** Absolute URL of the captured page. */
  url: string;
  title: string;
  viewport: SnapshotViewport;
  /** Document scroll offset when the shot was taken. */
  scroll: { x: number; y: number };
  /**
   * Stylesheets whose rules this origin may not read (cross-origin, no CORS).
   * Their `<link>` survives in the markup so the browser can still fetch them,
   * but a capture behind auth will not — the editor surfaces this as a warning
   * rather than shipping a silently unstyled screen.
   */
  blockedStyles: string[];
  /** Elements dropped or emptied for safety, for the same warning. */
  omitted: { frames: number; canvases: number; masked: number };
}

export interface CaptureOptions {
  /** Extra CSS selectors whose text content should be replaced with bullets. */
  maskSelectors?: string[];
  /** Replace every text node with same-shaped filler. Off by default. */
  redactText?: boolean;
}

// Attributes carrying a URL that must survive the move to another origin.
const URL_ATTRS = ['src', 'poster', 'data-src', 'xlink:href'];
// Never let the captured page's own behaviour cross into the replica.
const DROP_TAGS = new Set(['SCRIPT', 'NOSCRIPT', 'TEMPLATE']);
// Fields whose value is a secret even to the person recording the tour.
const SENSITIVE = 'input[type="password"],[data-stept-mask],[autocomplete^="cc-"]';

/** `•` runs of the same length, so a masked field keeps its visual weight. */
function bullets(value: string): string {
  return '•'.repeat(Math.min(value.length, 32));
}

function isElement(node: Node): node is Element {
  return node.nodeType === 1;
}

/**
 * Resolve `value` against `base`.
 *
 * Script-bearing schemes are erased rather than resolved — the sandbox already
 * blocks them, but an attribute that *reads* as executable is a trap for the
 * next person who mounts a replica somewhere looser. Opaque schemes (`data:`,
 * `blob:`) and non-navigations (`mailto:`, `tel:`, `#frag`) pass through: URL
 * resolution would turn them into garbage.
 */
export function absolutizeUrl(value: string, base: string): string {
  const raw = value.trim();
  if (/^(javascript|vbscript):/i.test(raw)) return '';
  if (!raw || raw.startsWith('#') || /^(data|blob|about|mailto|tel):/i.test(raw)) {
    return value;
  }
  try {
    return new URL(raw, base).href;
  } catch {
    return value;
  }
}

/** `srcset` is a comma-separated list of "url descriptor" pairs. */
export function absolutizeSrcset(value: string, base: string): string {
  return value
    .split(',')
    .map((candidate) => {
      const trimmed = candidate.trim();
      if (!trimmed) return '';
      const [url, ...descriptor] = trimmed.split(/\s+/);
      return [absolutizeUrl(url ?? '', base), ...descriptor].join(' ');
    })
    .filter(Boolean)
    .join(', ');
}

/** Rewrite every `url(...)` inside a CSS string so relative assets keep resolving. */
export function absolutizeCss(css: string, base: string): string {
  return css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (match, quote: string, url: string) => {
    const absolute = absolutizeUrl(url, base);
    return absolute === url ? match : `url(${quote}${absolute}${quote})`;
  });
}

/**
 * Stylesheet text this origin is allowed to read. Cross-origin sheets throw on
 * `cssRules` access; their href is reported instead so the caller can warn.
 */
export function collectStyles(doc: Document, base: string): { css: string[]; blocked: string[] } {
  const css: string[] = [];
  const blocked: string[] = [];
  for (const sheet of Array.from(doc.styleSheets)) {
    let rules: CSSRule[] | null = null;
    try {
      rules = Array.from((sheet as CSSStyleSheet).cssRules ?? []);
    } catch {
      rules = null;
    }
    if (rules === null) {
      const href = (sheet as CSSStyleSheet).href;
      if (href) blocked.push(href);
      continue;
    }
    const text = rules.map((rule) => rule.cssText).join('\n');
    if (text.trim()) css.push(absolutizeCss(text, base));
  }
  return { css, blocked };
}

/** Copy the live state of one element onto its clone. */
function inlineLiveState(source: Element, clone: Element, masked: () => void): void {
  const tag = source.tagName;
  if (tag === 'INPUT') {
    const input = source as HTMLInputElement;
    if (input.type === 'checkbox' || input.type === 'radio') {
      if (input.checked) clone.setAttribute('checked', '');
      else clone.removeAttribute('checked');
      return;
    }
    if (source.matches(SENSITIVE)) {
      clone.setAttribute('value', bullets(input.value));
      clone.setAttribute('type', 'text');
      masked();
      return;
    }
    clone.setAttribute('value', input.value);
    return;
  }
  if (tag === 'TEXTAREA') {
    const area = source as HTMLTextAreaElement;
    clone.textContent = source.matches(SENSITIVE) ? bullets(area.value) : area.value;
    return;
  }
  if (tag === 'OPTION') {
    if ((source as HTMLOptionElement).selected) clone.setAttribute('selected', '');
    else clone.removeAttribute('selected');
    return;
  }
  // Scroll offsets are not attributes; stash them so the player can restore.
  if (source.scrollTop > 0 || source.scrollLeft > 0) {
    clone.setAttribute('data-stept-scroll', `${Math.round(source.scrollLeft)},${Math.round(source.scrollTop)}`);
  }
}

/**
 * Freeze `doc` into a `PageSnapshot`.
 *
 * The original document is never mutated: everything happens on a deep clone,
 * walked index-aligned against the source (a `cloneNode(true)` is structurally
 * identical, so document-order element lists line up one-to-one).
 */
export function captureSnapshot(doc: Document, options: CaptureOptions = {}): PageSnapshot {
  const view = doc.defaultView;
  const base = doc.baseURI || doc.location?.href || 'about:blank';
  const omitted = { frames: 0, canvases: 0, masked: 0 };
  const countMasked = () => {
    omitted.masked += 1;
  };

  const root = doc.documentElement.cloneNode(true) as HTMLElement;
  const sources = Array.from(doc.documentElement.getElementsByTagName('*'));
  const clones = Array.from(root.getElementsByTagName('*'));
  const extraMask = (options.maskSelectors ?? []).join(',');

  // Collected first, then removed — mutating mid-walk desynchronizes the pairing.
  const doomed: Element[] = [];

  for (let i = 0; i < clones.length; i += 1) {
    const clone = clones[i]!;
    const source = sources[i];
    if (!source) break;

    if (DROP_TAGS.has(clone.tagName)) {
      doomed.push(clone);
      continue;
    }

    // Inline event handlers are dead weight in a script-free replica, and
    // leaving them in invites them to run if the sandbox is ever misconfigured.
    for (const attr of Array.from(clone.attributes)) {
      if (attr.name.toLowerCase().startsWith('on')) clone.removeAttribute(attr.name);
    }

    for (const attr of URL_ATTRS) {
      const value = clone.getAttribute(attr);
      if (value) clone.setAttribute(attr, absolutizeUrl(value, base));
    }
    const srcset = clone.getAttribute('srcset');
    if (srcset) clone.setAttribute('srcset', absolutizeSrcset(srcset, base));
    const inlineStyle = clone.getAttribute('style');
    if (inlineStyle && inlineStyle.includes('url(')) {
      clone.setAttribute('style', absolutizeCss(inlineStyle, base));
    }

    if (clone.tagName === 'A' || clone.tagName === 'AREA') {
      // A live href would navigate the iframe away from the replica on the
      // first stray click. Keep it as data so the editor can still show it.
      const href = clone.getAttribute('href');
      if (href) {
        clone.setAttribute('data-stept-href', absolutizeUrl(href, base));
        clone.removeAttribute('href');
      }
    }

    if (clone.tagName === 'FORM') {
      // The sandbox blocks submission outright; dropping the target keeps the
      // replica from advertising an endpoint it can never reach.
      clone.removeAttribute('action');
      clone.removeAttribute('target');
    }

    if (clone.tagName === 'LINK') {
      const rel = (clone.getAttribute('rel') ?? '').toLowerCase();
      const href = clone.getAttribute('href');
      if (href) clone.setAttribute('href', absolutizeUrl(href, base));
      // Preloads and prefetches only cost bandwidth in a static replica.
      if (rel.includes('preload') || rel.includes('prefetch') || rel.includes('modulepreload')) {
        doomed.push(clone);
      }
      continue;
    }

    if (clone.tagName === 'IFRAME' || clone.tagName === 'FRAME' || clone.tagName === 'OBJECT') {
      // Cross-origin frames are unreadable and same-origin ones would need
      // their own snapshot; either way, keep the space they occupied.
      const placeholder = doc.createElement('div');
      placeholder.setAttribute('data-stept-omitted', clone.tagName.toLowerCase());
      const rect = (source as HTMLElement).getBoundingClientRect?.();
      if (rect) placeholder.setAttribute('style', `width:${rect.width}px;height:${rect.height}px`);
      clone.replaceWith(placeholder);
      omitted.frames += 1;
      continue;
    }

    if (clone.tagName === 'CANVAS') {
      // Canvas pixels live outside the DOM, so they have to be rasterized now
      // or they are gone. A tainted canvas throws — accept the blank.
      try {
        const data = (source as HTMLCanvasElement).toDataURL('image/png');
        const img = doc.createElement('img');
        img.setAttribute('src', data);
        const rect = (source as HTMLElement).getBoundingClientRect?.();
        if (rect) img.setAttribute('style', `width:${rect.width}px;height:${rect.height}px`);
        clone.replaceWith(img);
      } catch {
        omitted.canvases += 1;
      }
      continue;
    }

    inlineLiveState(source, clone, countMasked);

    // Caller-supplied redaction: only leaf elements, so masking a container
    // blanks its copy instead of collapsing its whole subtree.
    if (extraMask && clone.children.length === 0 && clone.textContent && source.matches(extraMask)) {
      clone.textContent = bullets(clone.textContent);
      omitted.masked += 1;
    }
  }

  for (const node of doomed) node.remove();

  // Inlined stylesheets replace their own <link>, so nothing is applied twice.
  const { css, blocked } = collectStyles(doc, base);
  const blockedSet = new Set(blocked);
  for (const link of Array.from(root.querySelectorAll('link[rel~="stylesheet" i]'))) {
    const href = link.getAttribute('href');
    if (!href || !blockedSet.has(href)) link.remove();
  }
  for (const style of Array.from(root.querySelectorAll('style'))) style.remove();

  if (options.redactText) redactTextNodes(root);

  return {
    version: SNAPSHOT_VERSION,
    html: root.outerHTML,
    css,
    url: base,
    title: doc.title ?? '',
    viewport: {
      w: view?.innerWidth ?? doc.documentElement.clientWidth ?? 0,
      h: view?.innerHeight ?? doc.documentElement.clientHeight ?? 0,
    },
    scroll: { x: Math.round(view?.scrollX ?? 0), y: Math.round(view?.scrollY ?? 0) },
    blockedStyles: blocked,
    omitted,
  };
}

/** Replace visible copy with same-shaped filler, keeping layout honest. */
function redactTextNodes(root: Element): void {
  const walk = (node: Node): void => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = child.nodeValue ?? '';
        if (text.trim()) child.nodeValue = text.replace(/\S/g, '•');
      } else if (isElement(child)) {
        walk(child);
      }
    }
  };
  walk(root);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export interface RenderOptions {
  /** Extra CSS appended last — the player uses it to mark the tour's target. */
  extraCss?: string;
  /** Restore the captured scroll offset. On by default. */
  restoreScroll?: boolean;
}

/**
 * A `PageSnapshot` as one HTML document, ready for `iframe.srcdoc`.
 *
 * The CSP meta is defence in depth, not the boundary: the iframe's `sandbox`
 * attribute (see `SANDBOX_ATTR`) is what actually keeps this inert.
 */
export function renderSnapshot(snapshot: PageSnapshot, options: RenderOptions = {}): string {
  const restore = options.restoreScroll !== false;
  const css = snapshot.css.join('\n');
  const chrome = [
    // Anchors lost their href; keep them feeling clickable anyway.
    'a[data-stept-href]{cursor:pointer}',
    // Placeholders for what could not be captured read as deliberate gaps.
    '[data-stept-omitted]{background:repeating-linear-gradient(45deg,#0000,#0000 6px,#8881 6px,#8881 12px)}',
    restore && snapshot.scroll.y ? `html{scroll-behavior:auto}` : '',
    options.extraCss ?? '',
  ]
    .filter(Boolean)
    .join('\n');

  return [
    '<!doctype html>',
    '<meta charset="utf-8">',
    `<meta http-equiv="Content-Security-Policy" content="script-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'">`,
    `<title>${escapeHtml(snapshot.title)}</title>`,
    `<base href="${escapeHtml(snapshot.url)}">`,
    css ? `<style>${css}</style>` : '',
    chrome ? `<style>${chrome}</style>` : '',
    snapshot.html,
  ]
    .filter(Boolean)
    .join('\n');
}

/** Rough byte size of the stored envelope — the recorder warns before the 8 MB cap. */
export function snapshotBytes(snapshot: PageSnapshot): number {
  return new TextEncoder().encode(JSON.stringify(snapshot)).length;
}
