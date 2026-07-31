import { simpleProjection, targetFragility, type Target } from '@stept/dom-capture';
import type { RawEvent, StepAdvance, TourStep, TourStepType } from '../types';
import { isInternalUrl, suggestUrlPattern, urlPatternOf } from '../url-pattern';
import { deriveTitle, humanUrl, preview, quote, slugify, targetName } from './naming';
import { placementFor } from './placement';

/**
 * Deterministic RawEvent[] → TourStep[] compilation. Pure, no AI, no chrome.*.
 *
 * Ported from the old repo's `packages/compiler/src/index.ts`, keeping the
 * passes that still earn their keep and re-targeting the OUTPUT at step schema
 * v2 (docs/DAP2-CONTRACTS.md). Passes, in order:
 *
 *   1. `orderEvents`         — `chrome.runtime.sendMessage` gives NO ordering
 *                              guarantee, so restore causal order from `t`
 *                              (+ within a tick: input → key → everything else).
 *   2. `dropSyntheticSubmitClicks` — the browser's OWN click on a submit control
 *                              when Enter implicitly submits a form.
 *   3. `foldEnterKeys`       — an Enter adjacent to typing on the same field is
 *                              that typing's submit, in EITHER order.
 *   4. main loop             — typing coalescence, dblclick collapse, hover
 *                              dedupe, navigation → wait steps.
 *   5. hover fold + trailing-hover drop, trailing-wait drop.
 *   6. titling / slug / url-pattern suggestion, fragility lint.
 *
 * Event → step mapping (v2):
 *   click/dblclick/right-click → `tooltip`, advance `element_click`
 *   typed value                → `action {kind:"fill"}`, advance `input`
 *   typed SECRET               → `tooltip` "enter your own", advance `input`
 *                                (never an action: the value is not recorded)
 *   select                     → `tooltip`, advance `input`
 *   check / upload             → `tooltip`, advance `element_click`
 *   hover / keypress w/ target → `tooltip`, advance `button`
 *   keypress w/o target        → `modal`, advance `button`
 *   navigation                 → `wait {for:"url"}`
 *   scroll / download / tab    → absorbed (context only)
 */

export interface StepWarning {
  stepId: string;
  title: string;
  reason: string;
}

export interface CompileOptions {
  /** the page the recording was armed on — the url-pattern seed */
  startUrl?: string | null;
  titleOverrides?: Record<string, string>;
  bodyOverrides?: Record<string, string>;
  /** desired step order by id; ids absent keep their natural position */
  order?: readonly string[];
}

export interface CompileResult {
  steps: TourStep[];
  /** stepId → indexes of the raw events that produced it, so the panel can
   * delete a compiled step by removing its sources (ids stay deterministic). */
  sources: Record<string, number[]>;
  suggestedName: string;
  suggestedSlug: string;
  suggestedUrlPattern: string;
  /** record-time lint: steps anchored only to their position on the page */
  warnings: StepWarning[];
}

const WAIT_TIMEOUT_MS = 10_000;
const MAX_FALLBACKS = 5;

// ---------------------------------------------------------------------------
// pass 1: deterministic event ordering
// ---------------------------------------------------------------------------

/** Causal rank for events sharing a millisecond. `t` is stamped with
 * `Date.now()` (1ms granularity), so a fast type→Enter→submit burst can land
 * entirely inside one tick; a plain stable sort would then preserve ARRIVAL
 * order, which is what the unordered message channel scrambled. Within a tick
 * causality is knowable: you cannot submit before you typed, and the browser's
 * implicit-submission click comes after the Enter that caused it. */
function causalRank(kind: string): number {
  if (kind === 'input') return 0;
  if (kind === 'key') return 1;
  return 2;
}

/** Restore true DOM order from a permuted event array. Stable sort by
 * `(t, causalRank)`: events keep their relative arrival order unless the
 * timestamps — or, inside one millisecond, causality — say otherwise. Pure and
 * total: an already-ordered recording is returned unchanged. */
export function orderEvents<E extends { t: number; kind: string }>(events: readonly E[]): E[] {
  return events
    .map((ev, i) => ({ ev, i }))
    .sort((a, b) => a.ev.t - b.ev.t || causalRank(a.ev.kind) - causalRank(b.ev.kind) || a.i - b.i)
    .map((x) => x.ev);
}

// ---------------------------------------------------------------------------
// pass 2: the browser's own synthetic submit click
// ---------------------------------------------------------------------------

/** A click the BROWSER synthesized, not one the user made. Implicit form
 * submission (Enter in a text field) dispatches a click on the form's submit
 * control with no pointer behind it, so `clientX/clientY` are 0 — while every
 * real click carries the point it happened at. Requiring BOTH the 0,0 origin
 * AND a submit control keeps this narrow: a genuine click that happens to land
 * on the viewport corner is not a submit control, and a deliberate click on a
 * submit button has a real point. */
export function isSyntheticSubmitClick(ev: RawEvent): boolean {
  if (ev.kind !== 'pointer' || ev.action !== 'click') return false;
  if (ev.point.x !== 0 || ev.point.y !== 0) return false;
  const attrs = ev.context.fingerprint?.attrs ?? {};
  const tag = ev.context.fingerprint?.tagPath?.split('/').pop()?.toLowerCase();
  return (
    (attrs['type'] ?? '').toLowerCase() === 'submit' ||
    ((tag === 'button' || tag === 'input') && /submit/i.test(attrs['id'] ?? ''))
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

export function elementKey(target: Target | null | undefined): string {
  if (!target) return '';
  return (
    target.fingerprint?.elementHash ??
    target.selectors?.[0]?.value ??
    JSON.stringify(target.aria ?? {})
  );
}

function isModifierOnly(keys: string): boolean {
  return /^(Alt|Control|Ctrl|Meta|Shift)(\+(Alt|Control|Ctrl|Meta|Shift))*$/.test(keys);
}

/** Events that carry no causal weight when looking for an Enter's neighbour. */
function isNoise(kind: string): boolean {
  return kind === 'scroll' || kind === 'tab' || kind === 'download';
}

function noteDomain(domains: Set<string>, url: string | undefined): void {
  if (!url) return;
  try {
    domains.add(new URL(url).hostname);
  } catch {
    /* unparseable — ignore */
  }
}

interface Indexed {
  ev: RawEvent;
  i: number;
}

/** Build a targeted step from a recorded Target. Falls back to a `modal` step
 * when the projection produced no selector at all — the backend requires a
 * non-empty selector for tooltip/hotspot/action, and a step with no anchor is
 * still worth showing as a centred card. */
function targetedStep(
  id: string,
  type: Exclude<TourStepType, 'wait'>,
  target: Target,
  title: string,
  extra: Partial<TourStep> = {},
): TourStep {
  const projection = simpleProjection(target);
  const base: TourStep = {
    id,
    type: projection.selector ? type : 'modal',
    selector: projection.selector,
    fallback_selectors: projection.fallback_selectors.slice(0, MAX_FALLBACKS),
    text_hint: projection.text_hint,
    target,
    title,
    body: '',
    screenshot_key: null,
    placement: placementFor(target.bbox),
    advance: { on: 'button' },
    ...extra,
  };
  // a modal ignores its anchor: keep the advance sane if we downgraded
  if (base.type === 'modal' && base.advance.on === 'element_click') base.advance = { on: 'button' };
  return base;
}

function waitStep(id: string, urlPattern: string, title: string): TourStep {
  return {
    id,
    type: 'wait',
    selector: '',
    fallback_selectors: [],
    text_hint: '',
    target: null,
    title,
    body: '',
    screenshot_key: null,
    placement: 'auto',
    advance: { on: 'button' },
    wait: { for: 'url', url_pattern: urlPattern, timeout_ms: WAIT_TIMEOUT_MS },
  };
}

interface PendingInput {
  key: string;
  target: Target;
  value: string;
  secret: boolean;
  t: number;
  screenshotKey?: string;
  pressEnter: boolean;
  indexes: number[];
}

// ---------------------------------------------------------------------------
// compile
// ---------------------------------------------------------------------------

export function compile(events: readonly RawEvent[], opts: CompileOptions = {}): CompileResult {
  // pass 1: causal order, carrying each event's ORIGINAL index so `sources`
  // still points at the right rows in the recorder's array.
  const ordered: Indexed[] = events
    .map((ev, i) => ({ ev, i }))
    .sort(
      (a, b) =>
        a.ev.t - b.ev.t || causalRank(a.ev.kind) - causalRank(b.ev.kind) || a.i - b.i,
    );

  // pass 2: drop the browser's synthesized submit clicks
  const withoutSynthetic = ordered.filter((p) => !isSyntheticSubmitClick(p.ev));

  // pass 3: an Enter adjacent to typing on the SAME field is that typing's
  // submit — in either order (some sites emit the key before the coalesced
  // input). Record the pairing, then drop the standalone Enter.
  const enterFor = new Map<number, number>(); // input index → enter index
  const foldedEnters = new Set<number>();
  const meaningful = withoutSynthetic.filter((p) => !isNoise(p.ev.kind));
  for (let n = 0; n < meaningful.length; n++) {
    const here = meaningful[n];
    if (!here || here.ev.kind !== 'key' || here.ev.keys !== 'Enter' || !here.ev.context) continue;
    const key = elementKey(here.ev.context);
    for (const m of [n - 1, n + 1]) {
      const neighbour = meaningful[m];
      if (!neighbour || neighbour.ev.kind !== 'input') continue;
      if (elementKey(neighbour.ev.context) !== key) continue;
      if (enterFor.has(neighbour.i)) continue;
      enterFor.set(neighbour.i, here.i);
      foldedEnters.add(here.i);
      break;
    }
  }
  const stream = withoutSynthetic.filter((p) => !foldedEnters.has(p.i));

  const steps: TourStep[] = [];
  const sources: Record<string, number[]> = {};
  const domains = new Set<string>();
  const attribute = (stepId: string, ...idxs: number[]) => {
    (sources[stepId] ??= []).push(...idxs);
  };

  let counter = 0;
  const nextId = () => `s${++counter}`;

  let pendingInput: PendingInput | null = null;
  let sawAction = false;
  let entryUrl: string | null = opts.startUrl ?? null;

  const flushInput = () => {
    if (!pendingInput) return;
    const p = pendingInput;
    pendingInput = null;
    const name = targetName(p.target);
    const enterSuffix = p.pressEnter ? ' and press Enter' : '';
    const advance: StepAdvance = { on: 'input' };
    const step = p.secret
      ? targetedStep(nextId(), 'tooltip', p.target, `Enter your ${quote(name)}`, {
          advance,
          body: 'This value was kept private during recording — enter your own here.',
        })
      : targetedStep(
          nextId(),
          'action',
          p.target,
          `Type ${quote(preview(p.value))} into ${quote(name)}${enterSuffix}`,
          { advance, action: { kind: 'fill', value: p.value } },
        );
    if (p.screenshotKey) step.screenshot_key = p.screenshotKey;
    steps.push(step);
    attribute(step.id, ...p.indexes);
    sawAction = true;
  };

  for (const { ev, i } of stream) {
    switch (ev.kind) {
      case 'pointer': {
        flushInput();
        noteDomain(domains, ev.url);
        const key = elementKey(ev.context);
        const name = targetName(ev.context);

        if (ev.action === 'dblclick') {
          // browsers fire click,click,dblclick — collapse into one step
          const last = steps[steps.length - 1];
          if (last && last.type !== 'wait' && elementKey(last.target) === key) {
            last.title = `Double-click ${quote(name)}`;
            const prev = steps[steps.length - 2];
            if (prev && prev.type !== 'wait' && elementKey(prev.target) === key) {
              steps.splice(steps.length - 2, 1);
              attribute(last.id, ...(sources[prev.id] ?? []));
              delete sources[prev.id];
            }
            attribute(last.id, i);
            sawAction = true;
            break;
          }
        }

        const title =
          ev.action === 'context' || ev.button === 'right'
            ? `Right-click ${quote(name)}`
            : ev.action === 'dblclick'
              ? `Double-click ${quote(name)}`
              : `Click on ${quote(name)}`;
        const step = targetedStep(nextId(), 'tooltip', ev.context, title, {
          advance: { on: 'element_click' },
          screenshot_key: ev.screenshotKey ?? null,
        });
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'input': {
        const key = elementKey(ev.context);
        const prev = pendingInput as PendingInput | null;
        if (prev && prev.key !== key) flushInput();
        const same = prev && prev.key === key ? prev : null;
        const enterIdx = enterFor.get(i);
        pendingInput = {
          key,
          target: ev.context,
          value: ev.value,
          secret: ev.secret,
          t: ev.t,
          // keep the FIRST screenshot of the burst: it shows the empty field
          screenshotKey: same?.screenshotKey ?? ev.screenshotKey,
          pressEnter: same?.pressEnter || enterIdx !== undefined,
          indexes: [...(same?.indexes ?? []), i, ...(enterIdx !== undefined ? [enterIdx] : [])],
        };
        break;
      }

      case 'key': {
        if (isModifierOnly(ev.keys)) break;
        // the in-loop net for an Enter that arrived while typing was pending
        if (ev.keys === 'Enter' && pendingInput && ev.context && elementKey(ev.context) === pendingInput.key) {
          pendingInput.pressEnter = true;
          pendingInput.indexes.push(i);
          flushInput();
          break;
        }
        flushInput();
        const title = `Press ${ev.keys}`;
        const step = ev.context
          ? targetedStep(nextId(), 'tooltip', ev.context, title, { advance: { on: 'button' } })
          : ({
              id: nextId(),
              type: 'modal',
              selector: '',
              fallback_selectors: [],
              text_hint: '',
              target: null,
              title,
              body: '',
              screenshot_key: null,
              placement: 'center',
              advance: { on: 'button' },
            } satisfies TourStep);
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'select': {
        flushInput();
        const step = targetedStep(
          nextId(),
          'tooltip',
          ev.context,
          `Select ${quote(ev.label ?? ev.value)} in ${quote(targetName(ev.context))}`,
          { advance: { on: 'input' }, screenshot_key: ev.screenshotKey ?? null },
        );
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'check': {
        flushInput();
        const step = targetedStep(
          nextId(),
          'tooltip',
          ev.context,
          `${ev.checked ? 'Check' : 'Uncheck'} ${quote(targetName(ev.context))}`,
          { advance: { on: 'element_click' }, screenshot_key: ev.screenshotKey ?? null },
        );
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'upload': {
        flushInput();
        const step = targetedStep(
          nextId(),
          'tooltip',
          ev.context,
          `Upload ${quote(ev.fileName)}`,
          { advance: { on: 'element_click' }, screenshot_key: ev.screenshotKey ?? null },
        );
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'hover': {
        flushInput();
        const key = elementKey(ev.context);
        const last = steps[steps.length - 1];
        // a repeated hover on the same element is one step
        if (last && last.type !== 'wait' && elementKey(last.target) === key && last.title.startsWith('Hover')) {
          attribute(last.id, i);
          break;
        }
        const step = targetedStep(
          nextId(),
          'tooltip',
          ev.context,
          `Hover ${quote(targetName(ev.context))}`,
          { advance: { on: 'button' }, screenshot_key: ev.screenshotKey ?? null },
        );
        steps.push(step);
        attribute(step.id, i);
        sawAction = true;
        break;
      }

      case 'nav': {
        if (isInternalUrl(ev.url)) break;
        noteDomain(domains, ev.url);
        // Everything before the first real action defines where the tour
        // STARTS — that's the url_pattern, not a step.
        if (!sawAction) {
          entryUrl = ev.url;
          break;
        }
        const pattern = urlPatternOf(ev.url);
        const last = steps[steps.length - 1];
        // redirect hops (and repeat commits of the same route) fold into the
        // wait that started the chain: one step, the chain's final URL
        if (last && last.type === 'wait' && last.wait) {
          if (ev.redirect || last.wait.url_pattern === pattern) {
            last.wait.url_pattern = pattern;
            last.title = `Wait for ${humanUrl(ev.url)}`;
            attribute(last.id, i);
            break;
          }
        }
        if (ev.redirect) break; // a redirect with no wait to fold into is noise
        flushInput();
        const step = waitStep(nextId(), pattern, `Wait for ${humanUrl(ev.url)}`);
        steps.push(step);
        attribute(step.id, i);
        break;
      }

      case 'scroll':
      case 'download':
      case 'tab':
        break; // absorbed
    }
  }
  flushInput();

  // ---- pass 5: hover folds ------------------------------------------------
  // A hover immediately followed by an action on the SAME element is
  // redundant: the player scrolls/points at the target anyway. A hover that
  // revealed a DIFFERENT element (the real reveal chain: hover row → click the
  // icon that appeared) is kept.
  for (let i = steps.length - 2; i >= 0; i--) {
    const h = steps[i];
    const next = steps[i + 1];
    if (!h || !next || !h.title.startsWith('Hover') || h.type === 'wait') continue;
    if (next.type !== 'wait' && elementKey(h.target) === elementKey(next.target)) {
      attribute(next.id, ...(sources[h.id] ?? []));
      delete sources[h.id];
      steps.splice(i, 1);
    }
  }
  // A trailing hover is a drive-by: the user brushed a menu open on the way out
  // and never used what it revealed.
  while (steps.length > 0) {
    const last = steps[steps.length - 1];
    if (!last || !last.title.startsWith('Hover')) break;
    delete sources[last.id];
    steps.pop();
  }
  // A trailing wait has nothing to wait FOR — the tour ends there.
  while (steps.length > 0 && steps[steps.length - 1]?.type === 'wait') {
    const last = steps.pop();
    if (last) delete sources[last.id];
  }

  // ---- pass 6: overrides, ordering, titling, lint -------------------------
  const titleOverrides = opts.titleOverrides ?? {};
  const bodyOverrides = opts.bodyOverrides ?? {};
  for (const step of steps) {
    const t = titleOverrides[step.id];
    if (t !== undefined && t.trim()) step.title = t.trim();
    const b = bodyOverrides[step.id];
    if (b !== undefined) step.body = b;
  }

  const ordering = opts.order ?? [];
  if (ordering.length) {
    const rank = new Map(ordering.map((id, idx) => [id, idx]));
    const sorted = steps
      .map((s, idx) => ({ s, idx }))
      .sort((a, b) => (rank.get(a.s.id) ?? a.idx + 1e6) - (rank.get(b.s.id) ?? b.idx + 1e6))
      .map((x) => x.s);
    steps.splice(0, steps.length, ...sorted);
  }

  const warnings: StepWarning[] = [];
  for (const step of steps) {
    if (!step.target) continue;
    const frag = targetFragility(step.target);
    if (frag.level === 'weak') {
      warnings.push({ stepId: step.id, title: step.title, reason: frag.reason ?? 'fragile anchor' });
    }
  }

  const contentTitles = steps.filter((s) => s.type !== 'wait').map((s) => s.title);
  const suggestedName = deriveTitle(contentTitles, [...domains]);
  return {
    steps,
    sources,
    suggestedName,
    suggestedSlug: slugify(suggestedName),
    suggestedUrlPattern: suggestUrlPattern(entryUrl ?? ''),
    warnings,
  };
}
