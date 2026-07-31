import type { FrameRef, Hints, Target } from './types';
import { computeFingerprint } from './fingerprint';
import { generateSelectors } from './selectors';
import { axName, cssAttrValue, cssEscape, implicitRole, interactiveAncestor, normText, visibleText } from './util';

export interface BuildTargetOptions {
  /** Capture the enclosing interactive control instead of a throwaway inner
   * glyph (an `<svg><path>` inside a button). Default true. */
  climb?: boolean;
  /** Capture the same-origin frame path (top frame → the element's frame).
   * Default true; cross-origin hops end the walk without throwing. */
  frame?: boolean;
  /** Window used for the frame path + viewport size. Defaults to the element's
   * `ownerDocument.defaultView` (never touched at module scope). */
  view?: Window | null;
}

/** Build the full Target descriptor for an element — safe to call synchronously
 * inside a capture-phase pointerdown handler (pre-action DOM). */
export function buildTarget(el: Element, opts: BuildTargetOptions = {}): Target {
  const target = opts.climb === false ? el : interactiveAncestor(el);
  const role = implicitRole(target) || undefined;
  const name = axName(target) || undefined;
  const text = visibleText(target, 120);

  const rect = safeRect(target);
  const win = opts.view !== undefined ? opts.view : target.ownerDocument.defaultView;
  const frame = opts.frame === false ? [] : framePathOf(win);

  return {
    selectors: generateSelectors(target),
    text: text ? { content: text, exact: text.length < 60 } : undefined,
    aria: role || name ? { role, name } : undefined,
    hints: buildHints(target),
    fingerprint: computeFingerprint(target),
    frame,
    shadowPath: shadowPathOf(target),
    bbox: rect
      ? {
          x: rect.x,
          y: rect.y,
          w: rect.width,
          h: rect.height,
          viewport: win ? { w: win.innerWidth, h: win.innerHeight } : undefined,
        }
      : undefined,
  };
}

function safeRect(el: Element): DOMRect | null {
  try {
    const r = el.getBoundingClientRect();
    return r.width || r.height || r.x || r.y ? r : null;
  } catch {
    return null;
  }
}

function buildHints(el: Element): Hints | undefined {
  const container = containerHintOf(el);
  const position = positionHintOf(el);
  if (!container && !position) return undefined;
  return { container, position };
}

/** The nearest landmark/section label above an element — fieldset legend, an
 * aria-labelled landmark, or the leading heading of a section. Shared by capture
 * and resolution so a candidate's container is computed identically to the
 * recorded one (browser-use container_hint). */
export function containerHintOf(el: Element): string | undefined {
  let cur: Element | null = el.parentElement;
  while (cur) {
    if (cur.tagName === 'FIELDSET') {
      const legend = cur.querySelector('legend');
      if (legend) return normText(legend.textContent, 60);
    }
    const role = cur.getAttribute('role');
    const labelled = cur.getAttribute('aria-label');
    if (labelled && (role || /^(SECTION|FORM|NAV|ASIDE|HEADER|MAIN|DIALOG)$/.test(cur.tagName))) {
      return normText(labelled, 60);
    }
    if (cur.getAttribute('role') === 'dialog' || cur.getAttribute('role') === 'region') {
      const lb = cur.getAttribute('aria-labelledby');
      if (lb) {
        const t = normText(cur.ownerDocument.getElementById(lb)?.textContent, 60);
        if (t) return t;
      }
    }
    if (/^(SECTION|ARTICLE|FORM|ASIDE|MAIN|DIALOG)$/.test(cur.tagName) || cur.getAttribute('role') === 'dialog') {
      const heading = cur.querySelector('h1,h2,h3,h4,h5,h6,legend');
      // 4 = Node.DOCUMENT_POSITION_FOLLOWING (avoid touching the Node global)
      if (heading && heading.compareDocumentPosition(el) & 4) {
        return normText(heading.textContent, 60);
      }
    }
    cur = cur.parentElement;
  }
  return undefined;
}

/** "2 of 5" — the element's ordinal among same-tag siblings (position_hint). */
export function positionHintOf(el: Element): string | undefined {
  const parent = el.parentElement;
  if (!parent) return undefined;
  const similar = [...parent.children].filter((c) => c.tagName === el.tagName);
  return similar.length > 1 ? `${similar.indexOf(el) + 1} of ${similar.length}` : undefined;
}

/** The chain of shadow HOSTS from the outermost document down to the element's
 * root, e.g. `['#stept-widget', 'my-card']`. Undefined when the element is in
 * the light DOM. */
export function shadowPathOf(el: Element): string[] | undefined {
  const path: string[] = [];
  let root: Node = el.getRootNode();
  // 11 = DOCUMENT_FRAGMENT_NODE — duck-typed instead of `instanceof ShadowRoot`
  // so it also works across realms (an element from an iframe document).
  while (root.nodeType === 11 && (root as ShadowRoot).host) {
    const host = (root as ShadowRoot).host;
    const hostId = host.getAttribute('id');
    path.unshift(hostId ? `#${cssEscape(hostId)}` : host.tagName.toLowerCase());
    root = host.getRootNode();
  }
  return path.length ? path : undefined;
}

/** Same-origin frame path: selectors from the top frame down to `view`'s frame.
 * A cross-origin boundary ends the walk (each hop is individually guarded — a
 * partial path still helps); an empty array means "top frame". */
export function framePathOf(view: Window | null | undefined): FrameRef[] {
  const path: FrameRef[] = [];
  if (!view) return path;
  let win: Window = view;
  for (let hops = 0; hops < 20; hops++) {
    let frame: HTMLIFrameElement | null = null;
    try {
      const fe = win.frameElement;
      if (!fe) break; // top frame, or cross-origin parent (throws below instead)
      frame = fe as HTMLIFrameElement;
    } catch {
      break; // cross-origin parent — stop, keep what we have
    }
    try {
      const id = frame.getAttribute('id');
      const name = frame.getAttribute('name');
      path.unshift({
        selector: id
          ? `iframe#${cssEscape(id)}`
          : name
            ? `iframe[name="${cssAttrValue(name)}"]`
            : `iframe:nth-of-type(${nthOfType(frame)})`,
        name: name || undefined,
        urlPattern: frame.getAttribute('src') || undefined,
      });
    } catch {
      break;
    }
    try {
      const parent = win.parent;
      if (!parent || parent === win) break;
      win = parent;
    } catch {
      break; // cross-origin parent
    }
  }
  return path;
}

function nthOfType(el: Element): number {
  let n = 1;
  let sib = el.previousElementSibling;
  while (sib) {
    if (sib.tagName === el.tagName) n += 1;
    sib = sib.previousElementSibling;
  }
  return n;
}
