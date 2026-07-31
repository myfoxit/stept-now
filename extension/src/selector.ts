/**
 * Pure, DOM-in / string-out selector generator — the unit-tested core of the
 * recorder. No chrome.* usage, so it runs unchanged in the content script and
 * under jsdom in tests.
 *
 * Strategy (most stable first):
 *   1. `[data-tour="…"]`  — Stept's dedicated, author-controlled hook.
 *   2. `#id`              — when present and unique.
 *   3. shortest unique ancestor path built with `tag` + `:nth-of-type(n)`
 *      disambiguation, anchored on the nearest ancestor that carries a unique
 *      `data-tour` or `id`. Uniqueness is verified at every step via
 *      `querySelectorAll(sel).length === 1`.
 *
 * Class names are deliberately avoided: framework/utility classes (CSS-in-JS
 * hashes, Tailwind atoms) change between builds, whereas tag structure and
 * data-tour hooks are stable.
 */

const DATA_TOUR = 'data-tour';

/**
 * Canonical `CSS.escape` implementation (CSSOM serialize-an-identifier
 * algorithm). We ship our own instead of relying on the platform because jsdom
 * does not expose `CSS.escape`, and we want identical output in tests and in
 * the browser.
 */
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

/** Escape a string for use inside a double-quoted attribute-selector value. */
export function cssAttrValue(value: string): string {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function isUnique(selector: string, root: Document | Element): boolean {
  try {
    return root.querySelectorAll(selector).length === 1;
  } catch {
    // An invalid selector never counts as a unique match.
    return false;
  }
}

function dataTourSelector(el: Element): string | null {
  const value = el.getAttribute(DATA_TOUR);
  if (value == null || value === '') return null;
  return `[${DATA_TOUR}="${cssAttrValue(value)}"]`;
}

function idSelector(el: Element): string | null {
  if (!el.id) return null;
  return `#${cssEscape(el.id)}`;
}

/** Structural segment for one element: `tag`, plus `:nth-of-type(n)` when the
 *  element has same-tag siblings. */
function structuralSegment(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const parent = el.parentElement;
  if (!parent) return tag;
  const sameTag = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
  if (sameTag.length <= 1) return tag;
  const index = sameTag.indexOf(el) + 1;
  return `${tag}:nth-of-type(${index})`;
}

export interface SelectorOptions {
  /** Scope for uniqueness checks. Defaults to the element's owner document. */
  root?: Document | Element;
}

/**
 * Compute a robust, stable CSS selector that matches exactly one element
 * (`el`) within `root`.
 */
export function generateSelector(el: Element, options: SelectorOptions = {}): string {
  const root = options.root ?? el.ownerDocument ?? document;

  // 1. data-tour hook.
  const dataTour = dataTourSelector(el);
  if (dataTour && isUnique(dataTour, root)) return dataTour;

  // 2. id.
  const byId = idSelector(el);
  if (byId && isUnique(byId, root)) return byId;

  // 3. Build a path upward, returning as soon as it is unique.
  const parts: string[] = [];
  let current: Element | null = el;

  while (current) {
    let segment: string;

    // Prefer a globally-unique ancestor anchor (data-tour / id) to keep the
    // path short and resilient to structural changes above it.
    const anchor =
      current !== el ? (dataTourSelector(current) ?? idSelector(current)) : null;
    if (anchor && isUnique(anchor, root)) {
      segment = anchor;
    } else {
      segment = structuralSegment(current);
    }

    parts.unshift(segment);
    const candidate = parts.join(' > ');
    if (isUnique(candidate, root)) return candidate;

    current = current.parentElement;
  }

  // Fallback: the full structural path (best effort; may not be unique on a
  // pathological DOM, but is always a valid selector).
  return parts.join(' > ');
}

/**
 * Best-effort human-readable label for a clicked element, used to pre-fill a
 * step title in the popup. Pure DOM-in / string-out.
 */
export function elementTextHint(el: Element, maxLength = 80): string {
  const aria = el.getAttribute('aria-label');
  const title = el.getAttribute('title');
  const placeholder = el.getAttribute('placeholder');
  const value = el instanceof HTMLInputElement ? el.value : '';

  const raw =
    (aria && aria.trim()) ||
    (el.textContent ?? '').trim() ||
    (title ?? '').trim() ||
    (placeholder ?? '').trim() ||
    (value ?? '').trim();

  const text = raw.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 1).trimEnd() + '…';
}
