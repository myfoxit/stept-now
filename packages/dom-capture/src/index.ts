/**
 * @stept/dom-capture — the shared element capture + resolution engine.
 *
 * Ported from the old repo (selectors / fingerprint / resolve / capture / util)
 * per docs/DAP2-CONTRACTS.md. Plain TypeScript, zero runtime dependencies, no
 * `chrome.*`, no globals touched at module scope: the identical code runs in the
 * widget tour player, the Chrome extension recorder, and jsdom tests.
 *
 * Capture:  buildTarget(el) → Target (rich; the backend stores it as opaque JSON)
 * Project:  simpleProjection(target) → {selector, fallback_selectors, text_hint}
 * Resolve:  resolveTarget(doc, target) → ResolveResult (.element, .via, .healed)
 */

// ---- types (plain interfaces; the backend stores Target as opaque JSON) ----
export type {
  AriaInfo,
  BBox,
  Fingerprint,
  FrameRef,
  Hints,
  RankedSelector,
  SelectorKind,
  Target,
  TextInfo,
} from './types';

// ---- capture ----
export { buildTarget, containerHintOf, framePathOf, positionHintOf, shadowPathOf } from './capture';
export type { BuildTargetOptions } from './capture';

// ---- selectors ----
export {
  buildCssPath,
  buildXPath,
  generateSelectors,
  isStableId,
  isTestIdSelector,
  looksLikeVolatileText,
  queryBySelector,
  resolveSelector,
} from './selectors';

// ---- fingerprint ----
export { computeFingerprint, neighborTexts, serializeAttrs } from './fingerprint';

// ---- resolution ----
export { familyOfRole, resolveTarget, roleFamilyConflict, scoreCandidate, targetFragility } from './resolve';
export type { ResolveResult, ResolveVia } from './resolve';

// ---- backend projection ----
export { elementTextHint, serializeSelector, simpleProjection, textHintOf } from './projection';
export type { SimpleProjection } from './projection';

// ---- sandbox snapshots (DOM replica capture + render) ----
export {
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
export type { CaptureOptions, PageSnapshot, RenderOptions, SnapshotViewport } from './snapshot';

// ---- dom utilities (shared by recorder + player) ----
export {
  allElements,
  axName,
  candidateElements,
  CONTENT_ATTRS,
  cssAttrValue,
  cssEscape,
  fnv1a64,
  identityAttrs,
  implicitRole,
  interactiveAncestor,
  isDynamicClass,
  isInteractive,
  isVisibleLenient,
  levenshtein,
  normText,
  preferLaidOut,
  querySafe,
  stableClasses,
  STATIC_ATTRS,
  staticAttrs,
  strSim,
  structuralAttrs,
  tagPath,
  visibleText,
} from './util';
