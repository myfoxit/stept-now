import type { RankedSelector, Target } from './types';
import { normText } from './util';

/** The SIMPLE projection of a rich Target onto the three backend step fields
 * (docs/DAP2-CONTRACTS.md, shared decision 3). The full `target` object travels
 * alongside it as opaque JSON; this is what a player without the cascade — and
 * the tour editor's selector chips — work with. */
export interface SimpleProjection {
  /** primary selector (backend `step.selector`) */
  selector: string;
  /** ordered fallbacks, max 5 (backend `step.fallback_selectors`) */
  fallback_selectors: string[];
  /** normalized visible text, ≤80 chars (backend `step.text_hint`) */
  text_hint: string;
}

const MAX_FALLBACKS = 5;
const HINT_MAX = 80;

/** Serialize a RankedSelector to its DevTools-compatible string form: raw css,
 * or the kind-prefixed value (`aria/…`, `text/…`, `xpath/…`, `pierce/…`).
 * Idempotent — a value that already carries its prefix is returned unchanged. */
export function serializeSelector(sel: RankedSelector): string {
  if (sel.kind === 'css') return sel.value;
  return sel.value.startsWith(`${sel.kind}/`) ? sel.value : `${sel.kind}/${sel.value}`;
}

/** Project a Target onto `{selector, fallback_selectors, text_hint}`.
 *
 * - primary  = the best-scored `css` selector (a plain `querySelector` works for
 *   every player), else the best selector of any kind, prefix-serialized.
 * - fallbacks = the next up-to-5 css/aria/text values in score order (xpath and
 *   pierce stay in the rich `target` only — they need `resolveSelector`).
 * - text_hint = recorded visible text (else accessible name), ≤80 chars. */
export function simpleProjection(target: Target): SimpleProjection {
  const selectors = [...(target.selectors ?? [])].sort((a, b) => b.score - a.score);
  const primarySel = selectors.find((s) => s.kind === 'css') ?? selectors[0];
  const selector = primarySel ? serializeSelector(primarySel) : '';

  const fallback_selectors: string[] = [];
  for (const s of selectors) {
    if (s === primarySel) continue;
    if (s.kind !== 'css' && s.kind !== 'aria' && s.kind !== 'text') continue;
    const value = serializeSelector(s);
    if (!value || value === selector || fallback_selectors.includes(value)) continue;
    fallback_selectors.push(value);
    if (fallback_selectors.length >= MAX_FALLBACKS) break;
  }

  return { selector, fallback_selectors, text_hint: textHintOf(target) };
}

/** The step's text hint: recorded visible text, else the accessible name, else
 * the fingerprint's accessible name — normalized and capped at 80 chars. */
export function textHintOf(target: Target, maxLength = HINT_MAX): string {
  const raw = target.text?.content || target.aria?.name || target.fingerprint?.axName || '';
  return truncateHint(normText(raw, maxLength * 2), maxLength);
}

/**
 * Best-effort human-readable label for a clicked element, used to pre-fill a
 * step title and as the player's last-resort text scan. Pure DOM-in /
 * string-out (ported from the extension's selector.ts for continuity).
 */
export function elementTextHint(el: Element, maxLength = HINT_MAX): string {
  const aria = el.getAttribute('aria-label');
  const title = el.getAttribute('title');
  const placeholder = el.getAttribute('placeholder');
  const value = typeof (el as HTMLInputElement).value === 'string' ? (el as HTMLInputElement).value : '';

  const raw =
    (aria && aria.trim()) ||
    (el.textContent ?? '').trim() ||
    (title ?? '').trim() ||
    (placeholder ?? '').trim() ||
    (value ?? '').trim();

  return truncateHint(raw.replace(/\s+/g, ' ').trim(), maxLength);
}

function truncateHint(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 1).trimEnd() + '…';
}
