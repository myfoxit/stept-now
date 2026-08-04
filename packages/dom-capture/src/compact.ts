/**
 * Compact interactive-page view — what an AI "sees" when it drives a page.
 *
 * Ported from the old repo's `packages/dom-capture/compact.ts` +
 * `extension/src/overlay-priority.ts` (docs/research/old-extension-map.md). It
 * turns a live document into a numbered listing of everything that can be acted
 * on:
 *
 *     [0]<button role=button name="Save">
 *     [3]<input type=email placeholder="you@example.com" value="ada@…">
 *
 * The `[index]` is the address the model acts on (`page_act {index}`) and the
 * listing is char-budgeted + pageable, so a 2000-element app shell doesn't blow
 * the context window.
 *
 * Two ordering rules earn their keep on real apps:
 *  - an OPEN overlay (dialog/menu/listbox) is serialized first, so a modal's
 *    buttons can never be budgeted away by the 400-row table behind it;
 *  - then in-viewport elements, then the rest — what the user is looking at
 *    stays on page one instead of being buried under header/footer links.
 *
 * Pure DOM + string work: no `chrome.*`, no globals touched at module scope, so
 * the identical code runs in the widget, the extension and jsdom tests.
 */

import { axName, candidateElements, implicitRole, isVisibleLenient, normText, visibleText } from './util';

/** Selectors marking an overlay container — a transient surface the user is
 * interacting with right now, whose controls must not be budgeted away. */
export const OVERLAY_SELECTOR =
  '[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"],[aria-modal="true"]';

export interface IndexedElement {
  index: number;
  el: Element;
  tag: string;
  role: string;
  name: string;
  text: string;
  /** Current value of an input/textarea/select/contenteditable (capped; a
   * non-empty password reads as a mask, never the real value). */
  value?: string;
  /** Checked state — only set for checkables (checkbox/radio/aria-checked). */
  checked?: boolean;
  /** `disabled` attribute or `aria-disabled="true"`. */
  disabled?: boolean;
  /** `aria-expanded` — only set when the attribute is present. */
  expanded?: boolean;
}

/**
 * Live form state of an element — what the user (or a prior action) entered or
 * toggled, which the static tag/name serialization cannot show. Without it a
 * driving model re-types filled fields and re-toggles set checkboxes. Values are
 * capped so state can't blow the char budget; a non-empty password is masked.
 */
export function formState(
  el: Element,
): Pick<IndexedElement, 'value' | 'checked' | 'disabled' | 'expanded'> {
  const out: Pick<IndexedElement, 'value' | 'checked' | 'disabled' | 'expanded'> = {};
  const view = el.ownerDocument?.defaultView;
  const isInput = view ? el instanceof view.HTMLInputElement : false;
  const isTextArea = view ? el instanceof view.HTMLTextAreaElement : false;
  const isSelect = view ? el instanceof view.HTMLSelectElement : false;

  if (isInput) {
    const input = el as HTMLInputElement;
    if (input.type === 'checkbox' || input.type === 'radio') out.checked = input.checked;
    else if (input.value) out.value = input.type === 'password' ? '•••' : normText(input.value, 40);
  } else if (isTextArea) {
    const area = el as HTMLTextAreaElement;
    if (area.value) out.value = normText(area.value, 40);
  } else if (isSelect) {
    const select = el as HTMLSelectElement;
    const option = select.selectedOptions?.[0] ?? select.options[select.selectedIndex];
    const label = option ? normText(option.text, 40) : '';
    if (label) out.value = label;
  } else if ((el as HTMLElement).isContentEditable) {
    const text = normText((el as HTMLElement).innerText ?? el.textContent, 40);
    if (text) out.value = text;
  }

  const ariaChecked = el.getAttribute('aria-checked');
  if (out.checked === undefined && (ariaChecked === 'true' || ariaChecked === 'false')) {
    out.checked = ariaChecked === 'true';
  }
  if (
    (el as HTMLButtonElement | HTMLInputElement).disabled === true ||
    el.getAttribute('aria-disabled') === 'true'
  ) {
    out.disabled = true;
  }
  const expanded = el.getAttribute('aria-expanded');
  if (expanded === 'true' || expanded === 'false') out.expanded = expanded === 'true';
  return out;
}

// --- overlay-first / viewport-first ordering (pure, unit-tested) -------------

export interface OverlayRef {
  /** Stable key identifying the overlay container, so descendants group together. */
  key: string;
  /** Computed z-index of the container (0 when static/auto) — higher stacks on top. */
  z: number;
  /** Document order of the container — tie-breaks equal z-index. */
  order: number;
}

export interface OverlayCandidate {
  /** Nearest OPEN-overlay ancestor, or null when the candidate is ordinary page. */
  overlay?: OverlayRef | null;
  /** Does the candidate's client rect intersect the viewport right now? */
  inViewport?: boolean;
}

/**
 * The stacking-topmost overlay among the candidates, or null when none sit in
 * one. Topmost = greatest z-index, then greatest document order — so a submenu
 * over a dialog wins, and a freshly opened dialog wins over a persistent one
 * (e.g. a cookie banner that also uses `role="dialog"`).
 */
export function topmostOverlay(candidates: readonly OverlayCandidate[]): OverlayRef | null {
  let best: OverlayRef | null = null;
  for (const candidate of candidates) {
    const overlay = candidate.overlay;
    if (!overlay) continue;
    if (!best || overlay.z > best.z || (overlay.z === best.z && overlay.order > best.order)) {
      best = overlay;
    }
  }
  return best;
}

/**
 * Serialization order: the topmost open overlay's content first, then
 * in-viewport elements, then everything above/below the fold. Stable within each
 * group, so the numbering stays intuitive. A page with no overlay and no
 * viewport flags comes back unchanged (a copy).
 */
export function orderSnapshotElements<T extends OverlayCandidate>(candidates: T[]): T[] {
  const top = topmostOverlay(candidates);
  const overlay: T[] = [];
  const inView: T[] = [];
  const rest: T[] = [];
  for (const candidate of candidates) {
    if (top && candidate.overlay && candidate.overlay.key === top.key) overlay.push(candidate);
    else if (candidate.inViewport) inView.push(candidate);
    else rest.push(candidate);
  }
  return [...overlay, ...inView, ...rest];
}

// --- indexing ---------------------------------------------------------------

export interface IndexInteractiveOptions {
  /** Hard cap on indexed elements (every one gets an addressable index). */
  cap?: number;
  /** Elements to skip entirely — used to hide the widget's own UI from the AI. */
  exclude?: (el: Element) => boolean;
}

/** Attribute stamped on indexed elements so an action can re-find its target. */
export const INDEX_ATTR = 'data-stept-idx';

/**
 * Build the indexed interactive view of a document, overlay- and viewport-first.
 *
 * Zero-size and (near-)fully transparent candidates are dropped — they cannot
 * take a click, and listing them invites the model to try. `<option>` is exempt:
 * a closed select's options measure 0×0 by design yet stay actionable through
 * `select` on the parent. Below-the-fold elements are KEPT (they are
 * scrollable-to); the ordering merely demotes them.
 */
export function indexInteractive(doc: Document, options: IndexInteractiveOptions = {}): IndexedElement[] {
  const cap = options.cap ?? 800;
  const exclude = options.exclude;
  const view = doc.defaultView;
  const vw = view?.innerWidth ?? 0;
  const vh = view?.innerHeight ?? 0;
  const overlayOf = makeOverlayRefOf(doc);

  const flagged = candidateElements(doc)
    .filter((el) => !exclude?.(el))
    .map((el) => ({ el, overlay: overlayOf(el), rect: el.getBoundingClientRect() }))
    .filter(
      (candidate) =>
        candidate.el.tagName.toLowerCase() === 'option' ||
        (candidate.rect.width > 0 &&
          candidate.rect.height > 0 &&
          Number(view?.getComputedStyle(candidate.el).opacity || '1') > 0.05),
    )
    .map((candidate) => ({
      ...candidate,
      inViewport:
        vh === 0 ||
        (candidate.rect.bottom > 0 &&
          candidate.rect.right > 0 &&
          candidate.rect.top < vh &&
          candidate.rect.left < vw),
    }));

  const ordered = orderSnapshotElements(flagged).slice(0, cap);
  const index: IndexedElement[] = ordered.map((candidate, position) => ({
    index: position,
    el: candidate.el,
    tag: candidate.el.tagName.toLowerCase(),
    role: implicitRole(candidate.el),
    name: axName(candidate.el),
    text: visibleText(candidate.el, 80),
    ...formState(candidate.el),
  }));
  return index;
}

/** Stamp `data-stept-idx` on each indexed element (cleaning stale stamps first). */
export function stampIndex(doc: Document, index: readonly IndexedElement[]): void {
  doc.querySelectorAll(`[${INDEX_ATTR}]`).forEach((el) => el.removeAttribute(INDEX_ATTR));
  for (const entry of index) entry.el.setAttribute(INDEX_ATTR, String(entry.index));
}

/**
 * Per-snapshot overlay lookup: map each candidate to the nearest OPEN overlay
 * container it descends from, or null. The container's document-order index is
 * its stable key and order tiebreak; z-index comes from computed style.
 */
function makeOverlayRefOf(doc: Document): (el: Element) => OverlayRef | null {
  const containers = [...doc.querySelectorAll(OVERLAY_SELECTOR)];
  if (containers.length === 0) return () => null;
  const view = doc.defaultView;
  const refs = new Map<Element, OverlayRef>();
  containers.forEach((container, order) => {
    const raw = view?.getComputedStyle(container).zIndex ?? '';
    const z = Number.parseInt(raw, 10);
    refs.set(container, { key: `overlay:${order}`, z: Number.isFinite(z) ? z : 0, order });
  });
  return (el: Element) => {
    const container = el.closest(OVERLAY_SELECTOR);
    return (container && refs.get(container)) ?? null;
  };
}

// --- serialization ----------------------------------------------------------

/**
 * Serialize an index to the compact text the model reads:
 * `[i]<tag role=… name="…" value="…"> text` — one line per element, budgeted.
 *
 * `offset` pages a long index: serialization starts at that list position while
 * every line keeps its ORIGINAL `[index]` (so a pick from page 2 stays actable),
 * and the truncation marker names the offset to continue from.
 */
export function serializeCompact(
  index: readonly IndexedElement[],
  maxChars = 8000,
  offset = 0,
): string {
  const start = Math.max(0, Math.floor(offset));
  if (start > 0 && start >= index.length) {
    return `… (offset ${start} is past the end — the list has ${index.length} elements; take a fresh snapshot)`;
  }
  const lines: string[] = [];
  let used = 0;
  let position = start;
  for (; position < index.length; position++) {
    const entry = index[position]!;
    const attrs: string[] = [];
    if (entry.role && entry.role !== entry.tag) attrs.push(`role=${entry.role}`);
    if (entry.name) attrs.push(`name="${normText(entry.name, 60)}"`);
    const placeholder = entry.el.getAttribute('placeholder');
    if (placeholder) attrs.push(`placeholder="${normText(placeholder, 40)}"`);
    const type = entry.el.getAttribute('type');
    if (type && type !== entry.tag) attrs.push(`type=${type}`);
    if (entry.value) attrs.push(`value="${entry.value}"`);
    if (entry.checked !== undefined) attrs.push(`checked=${entry.checked}`);
    if (entry.disabled) attrs.push('disabled');
    if (entry.expanded !== undefined) attrs.push(`expanded=${entry.expanded}`);
    const text = entry.text && entry.text !== entry.name ? ` ${normText(entry.text, 60)}` : '';
    const line = `[${entry.index}]<${entry.tag}${attrs.length ? ' ' + attrs.join(' ') : ''}>${text}`;
    used += line.length + 1;
    if (used > maxChars && lines.length > 0) break;
    lines.push(line);
  }
  if (position < index.length) {
    lines.push(
      `… (${index.length - position} more elements — call page_snapshot with offset=${position} to continue)`,
    );
  }
  return lines.join('\n');
}

export interface FoundElement {
  index: number;
  tag: string;
  role: string;
  name: string;
  text: string;
  /** Is the match on screen right now, or does the page need scrolling first? */
  visible: boolean;
}

/**
 * Elements from an index whose accessible name or visible text contains
 * `query` (case-insensitive substring), best match first.
 *
 * Needed when the numbered listing is huge or paged: a model that knows it wants
 * "Add invoice" shouldn't have to read 800 lines to find its index. Exact-name
 * matches rank above prefix matches, which rank above substring hits, so
 * "Save" prefers the Save button over "Save and close".
 */
export function findByText(
  index: readonly IndexedElement[],
  query: string,
  limit = 10,
): FoundElement[] {
  const needle = normText(query, 120).toLowerCase();
  if (!needle) return [];
  const scored: Array<{ rank: number; entry: IndexedElement }> = [];
  for (const entry of index) {
    const name = entry.name.toLowerCase();
    const text = entry.text.toLowerCase();
    const value = (entry.value ?? '').toLowerCase();
    let rank = -1;
    if (name === needle) rank = 0;
    else if (text === needle) rank = 1;
    else if (name.startsWith(needle)) rank = 2;
    else if (name.includes(needle)) rank = 3;
    else if (text.includes(needle)) rank = 4;
    else if (value.includes(needle)) rank = 5;
    if (rank >= 0) scored.push({ rank, entry });
  }
  scored.sort((a, b) => a.rank - b.rank || a.entry.index - b.entry.index);
  return scored.slice(0, Math.max(1, limit)).map(({ entry }) => ({
    index: entry.index,
    tag: entry.tag,
    role: entry.role,
    name: entry.name,
    text: entry.text,
    visible: isOnScreen(entry.el),
  }));
}

/** Does the element's rect intersect the viewport right now? */
export function isOnScreen(el: Element): boolean {
  const view = el.ownerDocument?.defaultView;
  if (!view) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;
  return (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < (view.innerHeight || 0) &&
    rect.left < (view.innerWidth || 0)
  );
}

/** Tags that force a line break around their content when reading page text. */
const BLOCK_TAGS = new Set([
  'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'details', 'dialog', 'div', 'dl', 'dt',
  'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'header', 'hr', 'legend', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table',
  'td', 'textarea', 'th', 'tr', 'ul',
]);

/** Never read as content — invisible to the user, noise to the model. */
const SKIP_TAGS = new Set(['script', 'style', 'noscript', 'template', 'svg', 'head']);

/**
 * The page's visible text, for reading rather than clicking.
 *
 * The interactive listing answers "what can I click?"; it says nothing about
 * what the page SAYS — which is what verifying an action landed, or pulling a
 * value out of prose, needs.
 *
 * Walks the tree instead of reading `innerText`: hidden subtrees drop out, an
 * `exclude` predicate can hide the widget's own chrome, and block boundaries
 * become newlines. jsdom implements `innerText` as `textContent`, so the naive
 * path would run "Hello world" and "Second line" together — here the same code
 * produces the same shape in a browser and in tests.
 */
export function pageText(doc: Document, maxChars = 8000, exclude?: (el: Element) => boolean): string {
  const body = doc.body;
  if (!body) return '';
  const parts: string[] = [];

  const walk = (node: Node): void => {
    if (node.nodeType === 3) {
      const text = node.nodeValue ?? '';
      if (text.trim()) parts.push(text.replace(/[ \t\r\n]+/g, ' '));
      return;
    }
    if (node.nodeType !== 1) return;
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (SKIP_TAGS.has(tag) || exclude?.(el) || !isVisibleLenient(el)) return;
    const block = BLOCK_TAGS.has(tag);
    if (block) parts.push('\n');
    for (const child of el.childNodes) walk(child);
    if (block) parts.push('\n');
  };

  walk(body);
  const joined = parts
    .join('')
    .replace(/[ \t]+/g, ' ')
    .replace(/ ?\n ?/g, '\n')
    // One newline per block boundary: nested blocks each contribute a break, and
    // paragraph-vs-line distinction buys the model nothing but tokens.
    .replace(/\n{2,}/g, '\n')
    .trim();
  return joined.slice(0, maxChars);
}
