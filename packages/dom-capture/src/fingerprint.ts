import type { Fingerprint } from './types';
import { axName, fnv1a64, identityAttrs, normText, staticAttrs, structuralAttrs, tagPath } from './util';

/** browser-use's element-identity recipe with three DISTINCT tiers so
 * self-healing survives progressively more drift:
 *   elementHash    = tagPath | ALL static attrs | axName            (exact)
 *   stableHash     = tagPath | identity attrs (no content) | axName (content-attr drift)
 *   structuralHash = tagPath | structural attrs (no id, no content) | — (content+name+id drift)
 * The last one is what re-matches a content-varying element (an AI image whose
 * alt changes every run) by its structure alone — no LLM needed.
 *
 * Attribute serialization sorts keys, so a fingerprint is INDEPENDENT of the
 * order the attributes appear in the markup. */
export function computeFingerprint(el: Element): Fingerprint {
  const path = tagPath(el);
  const attrs = staticAttrs(el);
  const name = axName(el);

  return {
    elementHash: fnv1a64(`${path}|${serializeAttrs(attrs)}|ax=${name}`),
    stableHash: fnv1a64(`${path}|${serializeAttrs(identityAttrs(el))}|ax=${name}`),
    structuralHash: fnv1a64(`${path}|${serializeAttrs(structuralAttrs(el))}|`),
    tagPath: path,
    attrs,
    axName: name || undefined,
    neighborText: neighborTexts(el),
  };
}

/** Stable, order-independent serialization of an attribute bag. */
export function serializeAttrs(attrs: Record<string, string>): string {
  return Object.keys(attrs)
    .sort()
    .map((k) => `${k}=${attrs[k]}`)
    .join('&');
}

/** Up to `max` sibling texts around the element — cheap contextual evidence the
 * resolver uses to break ties between look-alike candidates. */
export function neighborTexts(el: Element, max = 3): string[] {
  const out: string[] = [];
  const parent = el.parentElement;
  if (!parent) return out;
  for (const sib of parent.children) {
    if (sib === el) continue;
    const t = normText(sib.textContent, 60);
    if (t) out.push(t);
    if (out.length >= max) break;
  }
  return out;
}
