/** Pure-DOM utilities shared by capture and resolution. No chrome.*, no node, no globals
 * touched at module scope — this file must run identically in content scripts, the widget
 * player, page.evaluate, and jsdom tests. */

/** Dynamic class tokens (browser-use DYNAMIC_CLASS_PATTERNS + css-in-js hashes):
 * excluded from fingerprints and generated selectors because they rot. */
const DYNAMIC_CLASS = [
  /focus|hover|active|selected|disabled|animat|transition|loading|open(ed)?$|closed|expand|collaps|visible|hidden|pressed|checked|highlight|current|enter|leave|dragging/i,
  /^(css|jss|sc|emotion|chakra)-/i,
  /^[a-z]+[-_][0-9a-f]{5,}$/i,
  /^[a-zA-Z0-9]{8,}$/, // single opaque hash token (no separators, mixed case/digits)
];

export function isDynamicClass(token: string): boolean {
  if (/^[a-z][a-z-]*$/.test(token) && token.length <= 24) {
    // plain kebab word(s): treat as stable unless it matches a state word
    return DYNAMIC_CLASS[0]!.test(token);
  }
  return DYNAMIC_CLASS.some((re) => re.test(token));
}

export function stableClasses(el: Element): string[] {
  return [...el.classList].filter((c) => !isDynamicClass(c)).slice(0, 4);
}

/** Attribute whitelist for fingerprints and CSS generation (browser-use STATIC_ATTRIBUTES). */
export const STATIC_ATTRS = [
  'id',
  'name',
  'type',
  'placeholder',
  'aria-label',
  'title',
  'role',
  'data-testid',
  'data-test',
  'data-cy',
  'data-qa',
  'for',
  'alt',
  'href',
] as const;

export function staticAttrs(el: Element): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of STATIC_ATTRS) {
    const v = el.getAttribute(a);
    if (v != null && v !== '') out[a] = a === 'href' ? hrefPath(v) : v.slice(0, 120);
  }
  const cls = stableClasses(el).join(' ');
  if (cls) out['class'] = cls;
  return out;
}

/** Content-y attributes that carry the element's *content* (labels, media,
 * links) and therefore drift when the content varies (an AI image's alt, a
 * result row's title). Kept out of the structure-only hashes so content-varying
 * elements still re-match by structure. */
export const CONTENT_ATTRS: readonly string[] = ['alt', 'title', 'aria-label', 'placeholder', 'href'];

/** staticAttrs minus content attrs — identity that survives content drift but
 * still keeps id, name, type, role, for, data-attrs and class. Backs stableHash. */
export function identityAttrs(el: Element): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(staticAttrs(el))) {
    if (!CONTENT_ATTRS.includes(k)) out[k] = v;
  }
  return out;
}

/** Pure structural identity — identityAttrs minus `id` (often framework
 * generated). Backs `structuralHash`: the content-free "same element in the
 * same place" signal (browser-use's parent_branch_hash spirit). */
export function structuralAttrs(el: Element): Record<string, string> {
  const out = identityAttrs(el);
  delete out['id'];
  return out;
}

function hrefPath(href: string): string {
  try {
    const u = new URL(href, 'http://x/');
    return u.pathname;
  } catch {
    return href.slice(0, 120);
  }
}

/** FNV-1a 64-bit — deterministic sync hash usable inside a pointerdown handler
 * (crypto.subtle is async). Identity quality is sufficient; not cryptographic. */
export function fnv1a64(input: string): string {
  let h = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  for (let i = 0; i < input.length; i++) {
    h ^= BigInt(input.charCodeAt(i));
    h = (h * prime) & 0xffffffffffffffffn;
  }
  return h.toString(16).padStart(16, '0');
}

export function normText(s: string | null | undefined, cap = 120): string {
  return (s ?? '').replace(/\s+/g, ' ').trim().slice(0, cap);
}

export function tagPath(el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && parts.length < 30) {
    parts.unshift(cur.tagName.toLowerCase());
    cur = cur.parentElement ?? ((cur.getRootNode() as ShadowRoot).host as Element | undefined) ?? null;
  }
  return parts.join('/');
}

export function visibleText(el: Element, cap = 120): string {
  const t = (el as HTMLElement).innerText ?? el.textContent ?? '';
  return normText(t, cap);
}

/** Simplified accessible-name computation (accname-lite). */
export function axName(el: Element): string {
  const doc = el.ownerDocument;
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const parts = labelledby
      .split(/\s+/)
      .map((id) => normText(doc.getElementById(id)?.textContent))
      .filter(Boolean);
    if (parts.length) return parts.join(' ').slice(0, 120);
  }
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel) return normText(ariaLabel);
  const id = el.getAttribute('id');
  if (id) {
    const esc = cssEscape(id);
    const forLabel = doc.querySelector(`label[for="${esc}"]`);
    if (forLabel) return normText(forLabel.textContent);
  }
  const parentLabel = el.closest('label');
  if (parentLabel) {
    const clone = parentLabel.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('input,select,textarea').forEach((n) => n.remove());
    const t = normText(clone.textContent);
    if (t) return t;
  }
  const tag = el.tagName.toLowerCase();
  if (tag === 'img') return normText(el.getAttribute('alt'));
  if (tag === 'input') {
    const input = el as HTMLInputElement;
    if (['button', 'submit', 'reset'].includes(input.type)) return normText(input.value);
    if (input.placeholder) return normText(input.placeholder);
  }
  if (el.getAttribute('placeholder')) return normText(el.getAttribute('placeholder'));
  if (['button', 'a', 'summary', 'option', 'label', 'th', 'td', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'legend'].includes(tag)) {
    const t = visibleText(el, 80);
    if (t) return t;
  }
  if (el.getAttribute('title')) return normText(el.getAttribute('title'));
  // last resort for small interactive containers
  if (isInteractive(el)) {
    const t = visibleText(el, 80);
    if (t && t.length <= 80) return t;
  }
  return '';
}

export function implicitRole(el: Element): string {
  const explicit = el.getAttribute('role');
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  switch (tag) {
    case 'a':
      return el.hasAttribute('href') ? 'link' : '';
    case 'button':
      return 'button';
    case 'select':
      return 'combobox';
    case 'textarea':
      return 'textbox';
    case 'img':
      return 'img';
    case 'nav':
      return 'navigation';
    case 'main':
      return 'main';
    case 'option':
      return 'option';
    case 'input': {
      const type = (el as HTMLInputElement).type;
      if (['button', 'submit', 'reset'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'search') return 'searchbox';
      if (['hidden'].includes(type)) return '';
      return 'textbox';
    }
    default:
      return '';
  }
}

const INTERACTIVE_TAGS = new Set(['a', 'button', 'input', 'select', 'textarea', 'option', 'summary', 'details', 'label']);
const INTERACTIVE_ROLES = new Set([
  'button', 'link', 'menuitem', 'option', 'radio', 'checkbox', 'tab', 'textbox', 'combobox',
  'slider', 'spinbutton', 'searchbox', 'switch', 'gridcell', 'row', 'cell', 'listbox',
]);

export function isInteractive(el: Element): boolean {
  const tag = el.tagName.toLowerCase();
  if (INTERACTIVE_TAGS.has(tag)) return true;
  const role = el.getAttribute('role');
  if (role && INTERACTIVE_ROLES.has(role)) return true;
  if (el.hasAttribute('onclick') || el.hasAttribute('tabindex')) return true;
  if ((el as HTMLElement).isContentEditable) return true;
  return false;
}

/** Extra interactive shapes worth climbing to that `isInteractive` misses:
 * menu/hover triggers (`aria-haspopup`), `summary`, `label`, and tab/menuitem
 * roles. Used only by `interactiveAncestor`. */
const EXTRA_INTERACTIVE_SEL =
  '[aria-haspopup],summary,label,[role="tab"],[role="menuitem"],[role="menuitemcheckbox"],[role="menuitemradio"],[role="switch"]';

/** Climb from a clicked/hovered node to the nearest interactive ancestor
 * (a button wrapping an `<svg><path>` icon, a `[role=menuitem]` around a glyph),
 * depth-capped. Recording the throwaway inner glyph gives a brittle target that
 * rots the moment the icon changes, whereas the enclosing control has stable
 * identity. Returns `el` unchanged when it is already interactive or no
 * interactive ancestor is within reach. */
export function interactiveAncestor(el: Element, maxDepth = 4): Element {
  let cur: Element | null = el;
  let depth = 0;
  while (cur && depth <= maxDepth && cur.nodeType === 1) {
    if (isInteractive(cur) || cur.matches?.(EXTRA_INTERACTIVE_SEL)) return cur;
    cur = cur.parentElement;
    depth++;
  }
  return el;
}

export function isVisibleLenient(el: Element): boolean {
  // Lenient: layout may be absent (jsdom). Only reject definitive hides.
  if ((el as HTMLElement).hidden) return false;
  if (el.getAttribute('aria-hidden') === 'true') return false;
  if (el instanceof HTMLInputElement && el.type === 'hidden') return false;
  const style = el.ownerDocument.defaultView?.getComputedStyle?.(el);
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
  return true;
}

/** Among candidates that already passed `isVisibleLenient`, prefer the ones the
 * actuator could actually click.
 *
 * `isVisibleLenient` ignores layout on purpose so resolution still works under
 * jsdom, but that leniency lets a ZERO-SIZE twin win a match. Real apps ship
 * these: ChatGPT's composer keeps a hidden <textarea> and a visible
 * contenteditable <div> that share the accessible name "Chat with ChatGPT", so
 * an aria fallback would resolve to the textarea, report a confident hit, and
 * then fail actuation with "element is not visible (zero-size rect)" — locate
 * accepting what act refuses.
 *
 * Preference, not a filter: when NO candidate reports layout (jsdom, or a
 * detached document) the original list is returned untouched, so headless
 * resolution behaves exactly as before. */
export function preferLaidOut<T extends Element>(els: T[]): T[] {
  if (els.length < 2) return els;
  const laidOut = els.filter((el) => {
    const r = el.getBoundingClientRect?.();
    return r ? r.width > 0 && r.height > 0 : false;
  });
  return laidOut.length > 0 ? laidOut : els;
}

/** Canonical `CSS.escape` (CSSOM serialize-an-identifier algorithm). We ship our
 * own instead of relying on the platform because jsdom does not expose
 * `CSS.escape`, and we want identical output in tests and in the browser. */
export function cssEscape(value: string): string {
  const str = String(value);
  const length = str.length;
  const firstCode = str.charCodeAt(0);
  let result = '';
  let index = -1;

  while (++index < length) {
    const code = str.charCodeAt(index);

    // NULL -> U+FFFD REPLACEMENT CHARACTER
    if (code === 0x0000) {
      result += '�';
      continue;
    }

    if (
      // control characters and DEL
      (code >= 0x0001 && code <= 0x001f) ||
      code === 0x007f ||
      // leading digit
      (index === 0 && code >= 0x0030 && code <= 0x0039) ||
      // digit immediately after a leading hyphen
      (index === 1 && code >= 0x0030 && code <= 0x0039 && firstCode === 0x002d)
    ) {
      result += '\\' + code.toString(16) + ' ';
      continue;
    }

    // a lone leading hyphen
    if (index === 0 && length === 1 && code === 0x002d) {
      result += '\\' + str.charAt(index);
      continue;
    }

    if (
      code >= 0x0080 || // non-ASCII
      code === 0x002d || // -
      code === 0x005f || // _
      (code >= 0x0030 && code <= 0x0039) || // 0-9
      (code >= 0x0041 && code <= 0x005a) || // A-Z
      (code >= 0x0061 && code <= 0x007a) // a-z
    ) {
      result += str.charAt(index);
      continue;
    }

    // any other character is escaped with a backslash
    result += '\\' + str.charAt(index);
  }

  return result;
}

/** Escape a string for use inside a double-quoted attribute-selector value.
 * (`cssEscape` is for identifiers and mangles spaces/colons inside a quoted string.) */
export function cssAttrValue(value: string): string {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

export function querySafe(root: ParentNode, selector: string): Element[] {
  try {
    return [...root.querySelectorAll(selector)];
  } catch {
    return [];
  }
}

/** Normalized string similarity in [0,1] = 1 − levenshtein/maxLen — the graded
 * comparator behind Similo's win over a binary equals. Two identical strings → 1;
 * a one-token/one-suffix drift stays high; unrelated strings fall toward 0. Bounds
 * the edit-distance work at 64 chars so a huge text blob can't dominate cost. */
export function strSim(a: string, b: string): number {
  if (a === b) return 1;
  const x = a.length > 64 ? a.slice(0, 64) : a;
  const y = b.length > 64 ? b.slice(0, 64) : b;
  const max = Math.max(x.length, y.length);
  if (max === 0) return 1;
  return 1 - levenshtein(x, y) / max;
}

/** Iterative Levenshtein with a single rolling row (O(min·max) time, O(min) space). */
export function levenshtein(a: string, b: string): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  // keep the shorter string as the row to minimize memory
  if (a.length > b.length) [a, b] = [b, a];
  let prev = Array.from({ length: a.length + 1 }, (_, i) => i);
  for (let j = 1; j <= b.length; j++) {
    const cur = [j];
    for (let i = 1; i <= a.length; i++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[i] = Math.min(prev[i]! + 1, cur[i - 1]! + 1, prev[i - 1]! + cost);
    }
    prev = cur;
  }
  return prev[a.length]!;
}

/** All elements including open shadow roots, documents in same-origin iframes excluded
 * (frames are handled structurally at a higher layer — Target.frame). */
export function allElements(root: ParentNode, cap = 20000): Element[] {
  const out: Element[] = [];
  const walk = (node: ParentNode) => {
    for (const el of node.querySelectorAll('*')) {
      if (out.length >= cap) return;
      out.push(el);
      const sr = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
      if (sr) walk(sr);
    }
  };
  walk(root);
  return out;
}

export function candidateElements(root: ParentNode): Element[] {
  return allElements(root).filter((el) => isInteractive(el) && isVisibleLenient(el));
}
