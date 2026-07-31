import type { RankedSelector, SelectorKind } from './types';
import {
  axName,
  cssAttrValue,
  cssEscape,
  implicitRole,
  isInteractive,
  normText,
  querySafe,
  stableClasses,
  visibleText,
} from './util';

const TESTID_ATTRS = ['data-testid', 'data-test', 'data-cy', 'data-qa'];

/** Generate the ranked selector stack for an element.
 * Order: testid → stable #id → aria → name attr → text → minimal css → xpath.
 * Every selector is uniqueness-checked against the document at record time. */
export function generateSelectors(el: Element): RankedSelector[] {
  const doc = el.ownerDocument;
  const out: RankedSelector[] = [];
  const push = (kind: SelectorKind, value: string, score: number) => {
    const unique = isUnique(doc, { kind, value });
    out.push({ kind, value, uniqueAtRecord: unique, score });
  };

  for (const attr of TESTID_ATTRS) {
    const v = el.getAttribute(attr);
    if (v) {
      // v2: test ids are plain css attribute selectors (the old dedicated
      // 'testid' kind folded into 'css'); `isTestIdSelector` recognises them.
      push('css', `[${attr}="${cssAttrValue(v)}"]`, 0.95);
      break;
    }
  }

  const tag = el.tagName.toLowerCase();
  const id = el.getAttribute('id');
  if (id && isStableId(id)) push('css', `#${cssEscape(id)}`, 0.9);
  else if (id) {
    // generated id → salvage its fixed stem as a prefix selector
    const stem = idStem(id);
    if (stem) push('css', `${tag}[id^="${cssAttrValue(stem)}"]`, 0.62);
  }

  const role = implicitRole(el);
  const name = axName(el);
  if (name && name.length <= 64) {
    // Chrome-Recorder-compatible: aria/Name[role="button"]. When the accessible
    // name is VOLATILE data (a comment count, a relative timestamp, a price), it
    // is specific to the recorded instance and won't match after the page's data
    // changes — demote it below the structural/position selectors so replay keys
    // off "the first result's link", not off "143 comments". (The value-specific
    // selector is kept as a lower fallback, not dropped.)
    push('aria', role ? `aria/${name}[role="${role}"]` : `aria/${name}`, looksLikeVolatileText(name) ? 0.5 : 0.85);
  }

  // Attribute-PREFIX selectors: generalize a content-y attribute (an AI image's
  // alt="Generated image: <varies>") to its stable prefix so replay survives
  // content drift — the deterministic answer to content-varying elements.
  for (const attr of ['alt', 'aria-label', 'title'] as const) {
    const v = el.getAttribute(attr);
    if (!v) continue;
    const prefix = stablePrefix(v);
    if (prefix && prefix.length < v.length) push('css', `${tag}[${attr}^="${cssAttrValue(prefix)}"]`, 0.76);
  }

  const nameAttr = el.getAttribute('name');
  if (nameAttr) push('css', `${tag}[name="${cssAttrValue(nameAttr)}"]`, 0.8);

  if (isInteractive(el)) {
    const text = visibleText(el, 40);
    // Same volatility demotion as aria: text that is a count/timestamp/price is
    // instance-specific and shouldn't be the primary key for replay.
    if (text && text.length >= 2 && text === normText(text, 40)) push('text', `text/${text}`, looksLikeVolatileText(text) ? 0.45 : 0.7);
  }

  // Container-anchored: <nearest durable ancestor> <tag> — identity survives the
  // leaf re-rendering / its content changing (Playwright anchor-then-descend).
  const scoped = containerAnchoredSelector(el);
  if (scoped) push('css', scoped, 0.72);

  const css = buildCssPath(el);
  if (css) push('css', css, 0.6);

  push('xpath', `xpath/${buildXPath(el)}`, 0.3);

  // De-duplicate identical values, keep highest score, stable order by score desc.
  const seen = new Map<string, RankedSelector>();
  for (const s of out) {
    const key = `${s.kind}:${s.value}`;
    if (!seen.has(key)) seen.set(key, s);
  }
  return [...seen.values()].sort((a, b) => b.score - a.score);
}

/** Does this accessible name / visible text look like VOLATILE instance data
 * rather than a stable label? Counts ("143 comments", "4.3k points"), relative
 * timestamps ("2 years ago"), prices/quantities ("$1,299", "48,000 credits"),
 * dates/times ("Oct 8, 2018", "14:30"), or a mostly-numeric token. Such names
 * identify the recorded instance, not the control's role, so a selector built
 * from them breaks the moment the underlying data changes. Deliberately
 * conservative — ordinary labels that merely contain a digit ("Web3", "H2",
 * "COVID-19", "Add 1 more") are NOT matched. */
export function looksLikeVolatileText(raw: string): boolean {
  const s = raw.trim();
  if (!s) return false;
  // count / metric next to a unit noun
  if (/\d[\d.,]*\s*[kKmMbB]?\+?\s*(comments?|points?|votes?|upvotes?|likes?|replies|reactions?|items?|results?|reviews?|ratings?|followers?|following|views?|watching|stars?|forks?|credits?)\b/i.test(s)) return true;
  // relative time
  if (/\b\d+\s*(sec|second|min|minute|hour|hr|day|week|month|year)s?\s+ago\b/i.test(s)) return true;
  // currency or percentage
  if (/[$€£¥₹]\s?\d|\b\d+(\.\d+)?\s*%/.test(s)) return true;
  // grouped thousands ("48,000", "1,299")
  if (/\b\d{1,3}(,\d{3})+\b/.test(s)) return true;
  // dates and clock times
  if (/\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b/i.test(s)) return true;
  // token that is essentially just a number (with separators/units)
  if (/^\W*\d[\d\s.,:/–-]*(?:[kKmMbB]|%|px|st|nd|rd|th)?\W*$/.test(s)) return true;
  return false;
}

/** Is this `id` an authored, durable handle — or a framework-generated token
 * (React useId, Radix, Headless UI, MUI, Angular, emotion, a uuid/counter/hash)
 * that changes on the next render? */
export function isStableId(id: string): boolean {
  if (!id || id.length > 64) return false;
  if (/^[0-9]+$/.test(id)) return false;
  // framework-generated id forms — matched as SUBSTRINGS (a prefix-only check
  // leaks React's `_r_…`/`«r»` forms and mid-string ids like `email-:r7:`)
  if (/:r[0-9a-z]+:/i.test(id)) return false; // React useId  :r0:
  if (/«r[0-9a-z]+»/i.test(id)) return false; // React useId  «r0»
  if (/(^|[^a-z0-9])_r_[0-9a-z]+_/i.test(id)) return false; // React useId  _r_0_
  if (/(^|[-_:])radix[-_:]/i.test(id)) return false;
  if (/(^|[-_])headlessui[-_]/i.test(id)) return false;
  if (/^mui-\d+$/i.test(id)) return false;
  if (/(^|[-_])downshift[-_]/i.test(id)) return false;
  if (/^ember\d+$/i.test(id)) return false;
  if (/^(mat|cdk)-\S*\d/i.test(id) || /^ng-tns-/.test(id) || /^_?ngcontent/i.test(id)) return false;
  if (/(^|[-_])css-[0-9a-z]{4,}/i.test(id)) return false; // emotion / css-in-js
  if (/[0-9a-f]{8}-[0-9a-f]{4}/i.test(id)) return false; // uuid
  if (/(^|[-_:])[0-9]{4,}/.test(id)) return false; // long counter
  if (/^[0-9a-f]{8,}$/i.test(id)) return false; // hex hash
  // opaque token: long, mixed-case, has digits, no word separators
  if (id.length >= 12 && /[0-9]/.test(id) && /[a-z]/.test(id) && /[A-Z]/.test(id) && !/[-_]/.test(id)) return false;
  return true;
}

/** True for `[data-testid="…"]`-style selectors — an app's explicit test/tour
 * contract, trusted like the old dedicated `testid` selector kind. */
export function isTestIdSelector(value: string | undefined): boolean {
  if (!value) return false;
  return TESTID_ATTRS.some((a) => value.trim().startsWith(`[${a}=`));
}

/** The stable leading part of a content-y attribute value: the label before a
 * "Label: value" colon, else the leading words before a generated-looking token
 * (`"Generated image: Autumn fox"` → `"Generated image"`). */
function stablePrefix(value: string): string | null {
  const v = value.trim();
  const colon = v.indexOf(':');
  if (colon >= 3 && colon <= 40) return v.slice(0, colon).trim();
  const kept: string[] = [];
  for (const w of v.split(/\s+/)) {
    if (/\d{3,}/.test(w) || /[0-9a-f]{6,}/i.test(w) || /^[0-9a-f_-]{8,}$/i.test(w)) break;
    kept.push(w);
    if (kept.join(' ').length >= 24) break;
  }
  const p = kept.join(' ').trim();
  return p.length >= 4 ? p : null;
}

/** Salvage the fixed stem of a generated id (`headlessui-menu-button-:r5:` →
 * `headlessui-menu-button-`) so it can back a `[id^="…"]` prefix selector. */
function idStem(id: string): string | null {
  const m = /^([A-Za-z][A-Za-z-]*[A-Za-z]-)(?=[0-9]|[:«_])/.exec(id);
  const stem = m?.[1];
  return stem && stem.length >= 4 ? stem : null;
}

/** A selector anchored at the nearest ancestor with a durable handle (testid /
 * stable id / labelled landmark) then descending by tag — identity that
 * survives the leaf re-rendering or its content changing (Playwright's
 * anchor-then-descend; browser-use's parent-branch identity). */
function containerAnchoredSelector(el: Element): string | null {
  const tag = el.tagName.toLowerCase();
  let cur = el.parentElement;
  let depth = 0;
  while (cur && depth < 6 && cur.tagName.toLowerCase() !== 'html') {
    let anchor: string | null = null;
    for (const a of TESTID_ATTRS) {
      const v = cur.getAttribute(a);
      if (v) {
        anchor = `[${a}="${cssAttrValue(v)}"]`;
        break;
      }
    }
    const cid = cur.getAttribute('id');
    if (!anchor && cid && isStableId(cid)) anchor = `#${cssEscape(cid)}`;
    const roleAttr = cur.getAttribute('role');
    const label = cur.getAttribute('aria-label');
    if (!anchor && roleAttr && label && label.length <= 40) {
      anchor = `${cur.tagName.toLowerCase()}[role="${cssAttrValue(roleAttr)}"][aria-label="${cssAttrValue(label)}"]`;
    }
    if (anchor) {
      const sel = `${anchor} ${tag}`;
      if (querySafe(el.ownerDocument, sel).length >= 1) return sel;
    }
    cur = cur.parentElement;
    depth++;
  }
  return null;
}

/** Minimal unique CSS path: per level tag + stable attrs/classes, :nth-of-type only
 * when needed, walk up max 5 levels (never through shadow boundaries — those are
 * represented structurally in Target.shadowPath). */
export function buildCssPath(el: Element): string | null {
  const doc = el.ownerDocument;
  const parts: string[] = [];
  let cur: Element | null = el;
  let depth = 0;
  while (cur && depth < 5 && cur.tagName.toLowerCase() !== 'html') {
    let part = cur.tagName.toLowerCase();
    const id = cur.getAttribute('id');
    if (id && isStableId(id)) {
      parts.unshift(`#${cssEscape(id)}`);
      break;
    }
    for (const attr of TESTID_ATTRS) {
      const v = cur.getAttribute(attr);
      if (v) {
        part += `[${attr}="${cssAttrValue(v)}"]`;
        break;
      }
    }
    const nameAttr = cur.getAttribute('name');
    if (nameAttr) part += `[name="${cssAttrValue(nameAttr)}"]`;
    const cls = stableClasses(cur).slice(0, 2);
    if (cls.length && part === cur.tagName.toLowerCase()) part += cls.map((c) => `.${cssEscape(c)}`).join('');

    const candidate = [part, ...parts].join(' > ');
    if (querySafe(doc, candidate).length === 1) {
      parts.unshift(part);
      break;
    }
    // disambiguate among same-tag siblings
    const parent = cur.parentElement;
    if (parent) {
      const sameTag = [...parent.children].filter((c) => c.tagName === cur!.tagName);
      if (sameTag.length > 1) part += `:nth-of-type(${sameTag.indexOf(cur) + 1})`;
    }
    parts.unshift(part);
    cur = cur.parentElement;
    depth++;
  }
  const sel = parts.join(' > ');
  return sel || null; // still useful as a fallback even when not unique
}

export function buildXPath(el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
    const tag = cur.tagName.toLowerCase();
    const parent: Element | null = cur.parentElement;
    if (!parent) break;
    const sameTag = [...parent.children].filter((c) => c.tagName === cur!.tagName);
    const idx = sameTag.indexOf(cur) + 1;
    parts.unshift(sameTag.length > 1 ? `${tag}[${idx}]` : tag);
    cur = parent;
  }
  return `/html/${parts.join('/')}`;
}

/** Resolve ONE selector of a given kind against a root, supporting all five
 * syntaxes (`css`, `aria/`, `text/`, `xpath/`, `pierce/`). Consumers call this
 * instead of parsing DevTools-style prefixes themselves. The prefix is optional:
 * `resolveSelector('text', 'Save', doc)` and `resolveSelector('text',
 * 'text/Save', doc)` behave identically. */
export function resolveSelector(kind: SelectorKind, value: string, root: ParentNode): Element[] {
  switch (kind) {
    case 'css':
      return querySafe(root, value);
    case 'aria': {
      const m = /^(?:aria\/)?(.+?)(?:\[role="([^"]+)"\])?$/.exec(value);
      if (!m) return [];
      const wantName = normText(m[1]).toLowerCase();
      const wantRole = m[2];
      return allInteractiveish(root).filter((el) => {
        if (wantRole && implicitRole(el) !== wantRole) return false;
        return normText(axName(el)).toLowerCase() === wantName;
      });
    }
    case 'text': {
      const want = normText(value.replace(/^text\//, '')).toLowerCase();
      if (!want) return [];
      const matches = allInteractiveish(root).filter((el) => visibleText(el, 200).toLowerCase().includes(want));
      // prefer the deepest/smallest matching elements
      return matches.filter((el) => !matches.some((other) => other !== el && el.contains(other)));
    }
    case 'xpath': {
      const expr = value.replace(/^xpath\//, '');
      const doc = docOf(root);
      if (!doc?.evaluate) return [];
      try {
        const res = doc.evaluate(expr, root as Node, null, 7 /* ORDERED_NODE_SNAPSHOT_TYPE */, null);
        const out: Element[] = [];
        for (let i = 0; i < res.snapshotLength; i++) {
          const n = res.snapshotItem(i);
          if (n && n.nodeType === 1) out.push(n as Element);
        }
        return out;
      } catch {
        return [];
      }
    }
    case 'pierce': {
      // recursive walk through OPEN shadow roots (closed roots are unreachable
      // by design — the page hid them from scripts)
      const inner = value.replace(/^pierce\//, '');
      const out: Element[] = [];
      const seen = new Set<ParentNode>();
      const walk = (node: ParentNode) => {
        if (seen.has(node)) return;
        seen.add(node);
        out.push(...querySafe(node, inner));
        for (const el of node.querySelectorAll('*')) {
          const sr = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
          if (sr) walk(sr);
        }
      };
      walk(root);
      return out;
    }
    default:
      return [];
  }
}

/** Query one RankedSelector against a root. Thin wrapper over `resolveSelector`
 * kept for the cascade (and for callers holding whole `RankedSelector`s). */
export function queryBySelector(root: ParentNode, sel: { kind: SelectorKind; value: string }): Element[] {
  return resolveSelector(sel.kind, sel.value, root);
}

function docOf(root: ParentNode): Document | null {
  const asDoc = root as Document;
  if (asDoc.nodeType === 9) return asDoc;
  return (root as Element).ownerDocument ?? null;
}

/** Elements an aria/text selector may bind to: interactive controls plus the
 * labelling shapes users click (headings, images). Walks open shadow roots. */
function allInteractiveish(root: ParentNode): Element[] {
  const out: Element[] = [];
  const walk = (node: ParentNode) => {
    for (const el of node.querySelectorAll('*')) {
      if (isInteractive(el) || /^h[1-6]$/i.test(el.tagName) || el.tagName === 'IMG') out.push(el);
      const sr = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
      if (sr) walk(sr);
    }
  };
  walk(root);
  return out;
}

function isUnique(doc: Document, sel: { kind: SelectorKind; value: string }): boolean {
  return queryBySelector(doc, sel).length === 1;
}
