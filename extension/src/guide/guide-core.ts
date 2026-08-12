import type { Target } from '@stept/dom-capture';
import { t } from '../i18n';
import { looksLikeSecret } from '../secret-redaction';
import { wildcardMatch } from '../url-pattern';
import type { PanelStepRef, TourStep } from '../types';

/** Pure logic behind guide mode ("show me"): what to tell the user for each
 * step, which in-page action completes it, and where the coach-mark tooltip
 * goes. Free of `chrome.*` and DOM globals so it unit-tests plain.
 *
 * Ported from the old repo's `guide-core.ts`; the adaptation is that
 * completion is derived from step schema v2 (`type` + `advance.on` + `action`)
 * instead of the old 16 step types, and the url-effect check reads a
 * `wait {for:"url"}` step instead of a recorded effect list.
 *
 * Guide mode never ACTS on the page — the user does (that is drive mode). The
 * overlay resolves the recorded target with the shared dom-capture cascade,
 * spotlights it, and advances when it observes the user perform the step.
 */

/** The sanitized slice of a step the overlay content script receives. Secrets
 * are stripped HERE, in the background, so a recorded credential never even
 * crosses into the page's isolated world. */
export interface GuideStepPayload {
  id: string;
  type: TourStep['type'];
  index: number;
  total: number;
  tourName: string;
  /** the recorded element to spotlight; absent → centered instruction card */
  target?: Target;
  /** primary selector, used when the step carries no rich target */
  selector?: string;
  fallbackSelectors?: string[];
  textHint?: string;
  /** bold lead line — the step's human title */
  instruction: string;
  /** muted support line (what to type, where this navigates, …) */
  detail?: string;
  /** markdown body, rendered as plain text in the overlay card */
  body?: string;
  /** literal text to type, shown as a copyable chip. Absent for secrets and
   * `{{variable}}` placeholders — those get an explanatory `detail` instead. */
  value?: string;
  /** true → the overlay watches for the user's action and auto-advances;
   * false → only the Next button moves on (modal/banner/wait steps) */
  waitsForAction: boolean;
  /** WHICH action completes the step, resolved here so the overlay never has
   * to re-derive it from a lossy reconstruction of the step. */
  spec: GuideActionSpec;
}

/** How the overlay recognizes "the user did the step". */
export type GuideActionSpec =
  | { on: 'pointerdown' }
  | { on: 'input' } // input on target, completed by Enter/blur
  | { on: 'delay'; ms: number }
  | { on: 'manual' }; // Next button only

const TEMPLATE_RE = /\{\{\s*[\w.-]+\s*\}\}/;

export function hasTemplate(text: string | undefined): boolean {
  return !!text && TEMPLATE_RE.test(text);
}

/** Strip {{var}} down to var for friendly copy ("your search-term"). */
function templateKey(text: string): string {
  const m = /\{\{\s*([\w.-]+)\s*\}\}/.exec(text);
  return m?.[1] ?? 'value';
}

/** Which real user action completes this step. `wait` steps are the engine's
 * (the background advances them off a URL match); `modal`/`banner` are read-only
 * cards; everything else follows the step's own `advance` rule. */
export function actionSpecFor(step: TourStep): GuideActionSpec {
  if (step.type === 'wait') return { on: 'manual' };
  if (step.type === 'modal' || step.type === 'banner') {
    return step.advance.on === 'delay'
      ? { on: 'delay', ms: step.advance.delay_ms ?? 3000 }
      : { on: 'manual' };
  }
  if (step.type === 'action' && step.action) {
    if (step.action.kind === 'click') return { on: 'pointerdown' };
    if (step.action.kind === 'fill') return { on: 'input' };
    return { on: 'manual' }; // navigate — the engine drives the tab
  }
  switch (step.advance.on) {
    case 'element_click':
      return { on: 'pointerdown' };
    case 'input':
      return { on: 'input' };
    case 'delay':
      return { on: 'delay', ms: step.advance.delay_ms ?? 3000 };
    default:
      return { on: 'manual' };
  }
}

/** Build the payload the overlay renders. `index`/`total` are the engine's own
 * counters, so the panel and the tooltip always agree. */
export function buildGuidePayload(
  step: TourStep,
  index: number,
  total: number,
  tourName: string,
): GuideStepPayload {
  const spec = actionSpecFor(step);
  const anchored = step.type !== 'modal' && step.type !== 'banner' && step.type !== 'wait';
  const payload: GuideStepPayload = {
    id: step.id,
    type: step.type,
    index,
    total,
    tourName,
    instruction: step.title || t('guide_core.step_number', { number: index + 1 }),
    body: step.body || undefined,
    waitsForAction: spec.on !== 'manual',
    spec,
  };
  if (anchored) {
    if (step.target) payload.target = step.target;
    payload.selector = step.selector || undefined;
    payload.fallbackSelectors = step.fallback_selectors?.length ? step.fallback_selectors : undefined;
    payload.textHint = step.text_hint || undefined;
  }

  if (step.type === 'action' && step.action) {
    if (step.action.kind === 'fill') {
      const value = step.action.value ?? '';
      const templated = hasTemplate(value);
      const secret = looksLikeSecret(value);
      payload.detail = secret
        ? t('guide_core.secret_detail')
        : templated
          ? t('guide_core.template_detail', { name: templateKey(value) })
          : t('guide_core.typing_detail');
      if (!secret && !templated && value) payload.value = value;
    } else if (step.action.kind === 'navigate') {
      payload.detail = step.action.url
        ? t('guide_core.navigate_detail', { url: step.action.url })
        : undefined;
    }
    return payload;
  }

  switch (step.type) {
    case 'wait':
      payload.detail =
        step.wait?.for === 'url' ? t('guide_core.wait_url_detail') : t('guide_core.wait_detail');
      break;
    case 'modal':
    case 'banner':
      payload.detail = payload.detail ?? undefined;
      break;
    default:
      if (spec.on === 'input') payload.detail = t('guide_core.input_detail');
      break;
  }
  return payload;
}

/** Does a committed navigation to `url` satisfy this step? Powers the
 * background's auto-advance when a wait-for-url step's page finally lands (and
 * when the user's click navigated before the overlay's "done" message got out). */
export function urlEffectMatches(step: Pick<TourStep, 'wait' | 'type'>, url: string): boolean {
  if (step.type !== 'wait') return false;
  const pattern = step.wait?.for === 'url' ? step.wait.url_pattern : undefined;
  return !!pattern && wildcardMatch(pattern, url);
}

/** "Control+k" / "Meta+Enter" / "Enter" vs a keydown-like event. Order and case
 * of modifiers are forgiving; the terminal token is the key itself. */
export function keyComboMatches(
  combo: string,
  ev: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; shiftKey?: boolean },
): boolean {
  const parts = combo
    .split('+')
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 0) return false;
  const key = parts[parts.length - 1] as string;
  const mods = new Set(parts.slice(0, -1).map((m) => m.toLowerCase()));
  const wants = (aliases: string[]) => aliases.some((a) => mods.has(a));
  const ctrl = wants(['ctrl', 'control']);
  const meta = wants(['meta', 'cmd', 'command']);
  const alt = wants(['alt', 'option']);
  const shift = wants(['shift']);
  if (ctrl !== !!ev.ctrlKey || meta !== !!ev.metaKey || alt !== !!ev.altKey) return false;
  // shift is only enforced when the combo names it: "?" is typed WITH shift
  if (shift && !ev.shiftKey) return false;
  return ev.key.toLowerCase() === key.toLowerCase();
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TooltipPlacement {
  x: number;
  y: number;
  side: 'above' | 'below' | 'left' | 'right' | 'floating';
}

/** Place the coach-mark near the spotlighted rect: below it when there is room,
 * above otherwise, and pinned bottom-center when the anchor is off-screen or
 * leaves no room either way. `prefer` comes from the step's recorded
 * `placement` and is honoured when it fits. All coordinates are viewport
 * (position: fixed) space. Ported from the old `placeTooltip`, widened for the
 * left/right placements step schema v2 can request. */
export function placeTooltip(
  anchor: Rect,
  tip: { w: number; h: number },
  viewport: { w: number; h: number },
  gap = 12,
  prefer: 'auto' | 'top' | 'bottom' | 'left' | 'right' | 'center' = 'auto',
): TooltipPlacement {
  const margin = 10;
  const clampX = (x: number) => Math.min(Math.max(x, margin), Math.max(margin, viewport.w - tip.w - margin));
  const clampY = (y: number) => Math.min(Math.max(y, margin), Math.max(margin, viewport.h - tip.h - margin));
  const offscreen =
    anchor.y + anchor.h < 0 || anchor.y > viewport.h || anchor.x + anchor.w < 0 || anchor.x > viewport.w;
  const floating = (): TooltipPlacement => ({
    x: clampX(viewport.w / 2 - tip.w / 2),
    y: Math.max(margin, viewport.h - tip.h - 24),
    side: 'floating',
  });
  if (offscreen || prefer === 'center') return floating();

  const centeredX = clampX(anchor.x + anchor.w / 2 - tip.w / 2);
  const centeredY = clampY(anchor.y + anchor.h / 2 - tip.h / 2);
  const fitsBelow = anchor.y + anchor.h + gap + tip.h <= viewport.h - margin;
  const fitsAbove = anchor.y - gap - tip.h >= margin;
  const fitsRight = anchor.x + anchor.w + gap + tip.w <= viewport.w - margin;
  const fitsLeft = anchor.x - gap - tip.w >= margin;

  const below = (): TooltipPlacement => ({ x: centeredX, y: anchor.y + anchor.h + gap, side: 'below' });
  const above = (): TooltipPlacement => ({ x: centeredX, y: anchor.y - gap - tip.h, side: 'above' });
  const right = (): TooltipPlacement => ({ x: anchor.x + anchor.w + gap, y: centeredY, side: 'right' });
  const left = (): TooltipPlacement => ({ x: anchor.x - gap - tip.w, y: centeredY, side: 'left' });

  if (prefer === 'bottom' && fitsBelow) return below();
  if (prefer === 'top' && fitsAbove) return above();
  if (prefer === 'right' && fitsRight) return right();
  if (prefer === 'left' && fitsLeft) return left();

  if (fitsBelow) return below();
  if (fitsAbove) return above();
  if (fitsRight) return right();
  if (fitsLeft) return left();
  return floating();
}

/** Trimmed step list for the panel's guide/drive progress view. */
export function panelSteps(steps: readonly TourStep[]): PanelStepRef[] {
  return steps.map((s) => ({ id: s.id, title: s.title, type: s.type }));
}
