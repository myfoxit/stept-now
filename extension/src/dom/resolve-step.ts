import {
  resolveTarget,
  type RankedSelector,
  type ResolveResult,
  type SelectorKind,
  type Target,
} from '@stept/dom-capture';

/** Shared step→element resolution for every in-page surface (guide overlay,
 * driver island, picker preview). One cascade, one confidence number, one
 * `healed` flag — exactly the engine the widget player uses, so a step that
 * resolves in one resolves in the other.
 *
 * A step may carry the rich `target` (recorded by this extension) or only the
 * simple projection `{selector, fallback_selectors, text_hint}` (hand-authored
 * in the dashboard). The simple case is lifted into a minimal Target so BOTH
 * run the same code path — never a bespoke second resolver.
 */

export interface StepTargetLike {
  target?: Target | null;
  selector?: string;
  fallbackSelectors?: string[];
  textHint?: string;
}

const PREFIXES: SelectorKind[] = ['aria', 'text', 'xpath', 'pierce'];

/** Split a stored selector string into its dom-capture kind + value. Selector
 * strings keep the DevTools-Recorder prefixes (`aria/`, `text/`, …); anything
 * unprefixed is plain CSS. */
export function parseSelector(value: string, score: number): RankedSelector {
  for (const kind of PREFIXES) {
    if (value.startsWith(`${kind}/`)) return { kind, value: value.slice(kind.length + 1), score };
  }
  return { kind: 'css', value, score };
}

/** Lift `{selector, fallbacks, text_hint}` into a Target the cascade can chew
 * on. Scores descend so the primary is tried first and the text hint last. */
export function minimalTarget(
  selector?: string,
  fallbacks: readonly string[] = [],
  textHint?: string,
): Target {
  const selectors: RankedSelector[] = [];
  if (selector) selectors.push(parseSelector(selector, 0.9));
  fallbacks.forEach((f, i) => {
    if (f) selectors.push(parseSelector(f, 0.8 - i * 0.05));
  });
  if (textHint) selectors.push({ kind: 'text', value: textHint, score: 0.45 });
  return {
    selectors,
    // The hint IS the element's label, so it feeds BOTH the text evidence and
    // the accessible-name tier of the cascade. Without the `aria.name`, a
    // hand-authored step whose selector rotted could never heal: a bare text
    // match scores ~0.25, below the 0.45 the verifier demands.
    ...(textHint
      ? { text: { content: textHint, exact: textHint.length <= 40 }, aria: { name: textHint } }
      : {}),
  };
}

/** Resolve a step's element, preferring the rich target and falling back to
 * the simple projection. Returns dom-capture's full result (element, via,
 * healed, confidence, tried) so callers can report `meta.healed`. */
export function resolveStepTarget(root: ParentNode, step: StepTargetLike): ResolveResult {
  if (step.target && (step.target.selectors?.length || step.target.fingerprint)) {
    return resolveTarget(root, step.target);
  }
  return resolveTarget(root, minimalTarget(step.selector, step.fallbackSelectors ?? [], step.textHint));
}
