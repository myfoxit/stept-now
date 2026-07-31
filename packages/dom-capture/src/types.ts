/**
 * Plain-TS contracts for @stept/dom-capture (docs/DAP2-CONTRACTS.md, shared decision 3).
 * These replace the old zod schemas from @stept/schema — field names are kept EXACTLY as
 * the old schema so the ported capture/resolve logic and previously recorded Target JSON
 * keep working. No runtime dependencies; the backend stores Target as opaque JSON.
 */

/** Selector kinds, ordered by typical durability. String syntaxes are
 * Chrome-DevTools-Recorder compatible (`aria/`, `text/`, `xpath/`, `pierce/`, css default)
 * so Recorder JSON imports/exports round-trip. Test-id selectors (`[data-testid="…"]`) are
 * plain css in v2 — the old dedicated 'testid' kind folded into 'css' per the contract. */
export type SelectorKind = 'css' | 'aria' | 'text' | 'xpath' | 'pierce';

/** One candidate selector with its durability score (higher = tried first). */
export interface RankedSelector {
  kind: SelectorKind;
  value: string;
  /** durability score in [0,1] — the ranked stack sorts by it, descending */
  score: number;
  /** did the selector match exactly one element at record time? (absent = true) */
  uniqueAtRecord?: boolean;
}

/** Visible text captured at record time. `exact` means short enough to equality-match. */
export interface TextInfo {
  content: string;
  exact?: boolean;
}

/** Accessibility identity: implicit/explicit role + accessible name (accname-lite). */
export interface AriaInfo {
  role?: string;
  name?: string;
}

/** Disambiguation hints from the recorded surroundings. */
export interface Hints {
  /** nearest landmark/section label above the element (fieldset legend, aria-label, heading) */
  container?: string;
  /** ordinal among same-tag siblings, e.g. "2 of 5" */
  position?: string;
}

/** browser-use-style element identity, captured pre-action (FNV-1a-64 hashes):
 *   elementHash    = tagPath | ALL static attrs | axName            (exact)
 *   stableHash     = tagPath | identity attrs (no content) | axName (survives content-attr drift)
 *   structuralHash = tagPath | structural attrs (no id/content) | — (survives content+name+id drift)
 */
export interface Fingerprint {
  elementHash: string;
  stableHash: string;
  /** content-free structural identity — optional for back-compat with old recordings */
  structuralHash?: string;
  tagPath: string;
  attrs: Record<string, string>;
  axName?: string;
  neighborText: string[];
}

/** One hop of the same-origin frame path, top frame down to the element's frame. */
export interface FrameRef {
  selector?: string;
  name?: string;
  urlPattern?: string;
}

/** Element geometry at record time (viewport-relative CSS px). */
export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
  viewport?: { w: number; h: number };
}

/** Everything the recorder can know about an element — the healing fuel. */
export interface Target {
  selectors: RankedSelector[];
  text?: TextInfo;
  aria?: AriaInfo;
  hints?: Hints;
  fingerprint?: Fingerprint;
  frame?: FrameRef[];
  shadowPath?: string[];
  bbox?: BBox;
}
