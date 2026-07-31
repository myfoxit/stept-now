import type { Target } from './types';
import { containerHintOf, positionHintOf } from './capture';
import { computeFingerprint, neighborTexts } from './fingerprint';
import { isTestIdSelector, queryBySelector } from './selectors';
import {
  axName,
  candidateElements,
  implicitRole,
  isVisibleLenient,
  normText,
  preferLaidOut,
  strSim,
  visibleText,
} from './util';

/** Which tier of the cascade produced the hit:
 *  - `primary`     L0 — the top-ranked selector, verified
 *  - `fallback`    L1a — a lower-ranked selector, verified
 *  - `fingerprint` L1b/c/d — recorded identity (hashes, accessible name, unique attr)
 *  - `scored`      L2 — structural similarity scoring
 *  - `none`        nothing resolved confidently */
export type ResolveVia = 'primary' | 'fallback' | 'fingerprint' | 'scored' | 'none';

export interface ResolveResult {
  element: Element | null;
  /** healing level that produced the hit: 0 primary selector, 1 fallback/identity, 2 structural */
  level: 0 | 1 | 2;
  /** which tier matched — `healed` is derived from this */
  via: ResolveVia;
  /** true when an element was found by anything OTHER than the primary selector.
   * Players report `step_viewed` with `meta.healed=true` for these. */
  healed: boolean;
  /** L2 structural score, when scoring produced the hit */
  score?: number;
  /** 0..1 how much to trust this hit for a BLIND actuation. A verified
   * positional/xpath handle still resolves but scores low, so a runner can heal
   * to a stabler match BEFORE clicking rather than trusting a brittle
   * nth-child. 1 = selector contract. */
  confidence: number;
  /** human-readable tier detail, e.g. `fallback aria hit`, `STABLE hash` */
  detail: string;
  /** ordered trace of everything the cascade tried (debugging / editor UI) */
  tried: string[];
}

/** The L0–L2 resolution cascade. Zero AI. Selector hits are VERIFIED against the
 * recorded fingerprint before being trusted — never a blind race. */
export function resolveTarget(root: ParentNode, target: Target): ResolveResult {
  const tried: string[] = [];
  const selectors = target.selectors ?? [];

  // L0: primary selector + sanity
  const primary = selectors[0];
  if (primary) {
    const els = preferLaidOut(queryBySelector(root, primary).filter(isVisibleLenient));
    tried.push(`L0 ${primary.kind}:${primary.value} → ${els.length}`);
    const pick = pickVerified(els, target, primary.kind, primary.value);
    if (pick) return hit(pick.el, 0, 'primary', selectorConfidence(primary, pick.ambiguous), `primary ${primary.kind} hit`, tried);
  }

  // L1a: ranked fallbacks, each verified
  for (const sel of selectors.slice(1)) {
    const els = preferLaidOut(queryBySelector(root, sel).filter(isVisibleLenient));
    tried.push(`L1 ${sel.kind}:${sel.value.slice(0, 60)} → ${els.length}`);
    const pick = pickVerified(els, target, sel.kind, sel.value);
    if (pick) return hit(pick.el, 1, 'fallback', selectorConfidence(sel, pick.ambiguous), `fallback ${sel.kind} hit`, tried);
  }

  // L1b: fingerprint-hash cascade over indexed candidates (browser-use recipe)
  const fp = target.fingerprint;
  const candidates = candidateElements(root);
  if (fp) {
    const byHash = (kind: 'elementHash' | 'stableHash') =>
      candidates.find((el) => computeFingerprint(el)[kind] === fp[kind]);
    const exact = byHash('elementHash');
    tried.push(`L1 elementHash → ${exact ? 'hit' : 'miss'}`);
    if (exact) return hit(exact, 1, 'fingerprint', 0.97, 'EXACT hash', tried);
    const stable = byHash('stableHash');
    tried.push(`L1 stableHash → ${stable ? 'hit' : 'miss'}`);
    if (stable) return hit(stable, 1, 'fingerprint', 0.9, 'STABLE hash', tried);
    // structuralHash: content-free identity (survives alt/name/id drift). A
    // structure-only hash can collide across siblings, so trust it ONLY when a
    // single candidate matches — otherwise fall through to scoring.
    if (fp.structuralHash) {
      const structural = candidates.filter((el) => computeFingerprint(el).structuralHash === fp.structuralHash);
      tried.push(`L1 structuralHash → ${structural.length} match${structural.length === 1 ? '' : 'es'}`);
      if (structural.length === 1) return hit(structural[0]!, 1, 'fingerprint', 0.85, 'STRUCTURAL hash', tried);
    }
  }

  // L1c: same tag + accessible name. When several elements share the name
  // (e.g. two "Create" buttons), disambiguate by recorded container/position
  // instead of blindly taking the first — that ambiguity is precisely what used
  // to fall through to AI healing on otherwise-simple pages.
  const wantName = normText(target.aria?.name ?? '', 80).toLowerCase();
  const wantTag = fp ? fp.tagPath.split('/').pop() : undefined;
  if (wantName) {
    const named = candidates.filter(
      (el) =>
        (!wantTag || el.tagName.toLowerCase() === wantTag) &&
        normText(axName(el)).toLowerCase() === wantName &&
        !roleFamilyConflict(el, target),
    );
    const found = named.length === 1 ? named[0] : named.length > 1 ? disambiguate(named, target) : undefined;
    tried.push(`L1 tag+axName("${wantName}") → ${named.length} match${named.length === 1 ? '' : 'es'}${found ? ' (resolved)' : ''}`);
    if (found) {
      const conf = named.length > 1 ? 0.8 : 0.85;
      return hit(found, 1, 'fingerprint', conf, named.length > 1 ? 'AX name + container' : 'AX name', tried);
    }
  }

  // L1d: unique static attr (id/name/aria-label)
  if (fp) {
    for (const key of ['id', 'name', 'aria-label'] as const) {
      const v = fp.attrs[key];
      if (!v) continue;
      const hits = candidates.filter((el) => el.getAttribute(key) === v);
      if (hits.length === 1) {
        tried.push(`L1 unique [${key}="${v}"] → hit`);
        return hit(hits[0]!, 1, 'fingerprint', 0.9, `unique attr ${key}`, tried);
      }
    }
  }

  // L2: structural scoring (role+name .35, text .25, attrs .20, tagPath .15, geometry .05)
  let best: { el: Element; score: number } | null = null;
  let second = 0;
  for (const el of candidates) {
    const s = scoreCandidate(el, target);
    if (!best || s > best.score) {
      second = best?.score ?? 0;
      best = { el, score: s };
    } else if (s > second) {
      second = s;
    }
  }
  if (best) {
    tried.push(`L2 best ${best.score.toFixed(2)} (runner-up ${second.toFixed(2)})`);
    if (best.score >= 0.8 && best.score - second >= 0.1) {
      const res = hit(best.el, 2, 'scored', best.score, `structural ${best.score.toFixed(2)}`, tried);
      res.score = best.score;
      return res;
    }
  }

  return { element: null, level: 2, via: 'none', healed: false, confidence: 0, detail: 'no confident match', tried };
}

function hit(el: Element, level: 0 | 1 | 2, via: ResolveVia, confidence: number, detail: string, tried: string[]): ResolveResult {
  return { element: el, level, via, healed: via !== 'primary', confidence, detail, tried };
}

/** A verified selector match plus whether the selector was non-unique (matched
 * several valid elements and we ranked among them) — the ambiguous case is
 * trusted a little less for a blind click. */
interface VerifiedPick {
  el: Element;
  ambiguous: boolean;
}

function pickVerified(els: Element[], target: Target, kind: string, value?: string): VerifiedPick | null {
  if (els.length === 0) return null;
  const verified = els.filter((el) => verify(el, target, kind, els.length === 1, value));
  if (verified.length === 0) return null;
  if (verified.length === 1) return { el: verified[0]!, ambiguous: false };
  // A non-unique selector matched several valid elements (a text/aria selector
  // often does). Don't blindly take the first — rank by the full target score
  // (container/neighbor aware) so we act on the one the user actually recorded,
  // not its lookalike elsewhere on the page.
  const el = verified.map((e) => ({ el: e, s: scoreCandidate(e, target) })).sort((a, b) => b.s - a.s)[0]!.el;
  return { el, ambiguous: true };
}

/** A pure tag + attribute selector with no descendant/positional combinators —
 * `img[alt^="x"]`, `[name="q"]`, `input[type="email"]`. Unlike a css *path*, its
 * match IS its identity evidence, so a unique hit can be trusted like a testid. */
function isAttributeSelector(value: string): boolean {
  return /^[a-z0-9-]*(\[[^\]]+\])+$/i.test(value.trim());
}

/** Record-time fragility check: does this target carry any DURABLE anchor, or is
 * its only handle a brittle positional css / xpath / structure hash that rots
 * the moment the layout shifts? Used to warn the author at stop-recording so a
 * flaky step never silently ships. */
export function targetFragility(target: Target): { level: 'ok' | 'weak'; reason?: string } {
  const hasStrongSelector = (target.selectors ?? []).some((s) => {
    if (s.kind === 'aria' || s.kind === 'text') return true;
    if (s.kind === 'css') {
      if (isTestIdSelector(s.value)) return true;
      if (isPositionalSelector(s.value)) return false;
      return isAttributeSelector(s.value) || /^#[\w-]+$/.test(s.value) || s.uniqueAtRecord !== false;
    }
    return false; // xpath / pierce are last-ditch, never "strong"
  });
  const hasName = !!(target.aria?.name || target.fingerprint?.axName || target.text?.content);
  const attrs = target.fingerprint?.attrs ?? {};
  const hasStaticAttr = ['id', 'name', 'data-testid', 'data-test', 'aria-label'].some((k) => attrs[k]);
  if (hasStrongSelector || hasName || hasStaticAttr) return { level: 'ok' };
  return {
    level: 'weak',
    reason:
      'anchored only to its position on the page — this step may break if the layout changes. Re-record it, or pick an element with a clearer label.',
  };
}

/** A brittle *positional* selector — a `:nth-*` pseudo, or a descendant/child
 * path of 3+ bare tag steps. One inserted wrapper shifts the index and it now
 * points at the wrong node, so a unique match is NOT identity evidence. It still
 * resolves, but at low confidence so the runner heals to a stabler handle. */
function isPositionalSelector(value: string): boolean {
  if (/:nth-(of-type|child|last-child|last-of-type)\b/.test(value)) return true;
  const steps = value.trim().split(/\s*[>\s]\s*/).filter(Boolean);
  const bareTagSteps = steps.filter((s) => /^[a-z][a-z0-9]*$/i.test(s)).length;
  return steps.length >= 3 && bareTagSteps >= 3;
}

/** How much a verified selector hit should be trusted for a BLIND click.
 * Identity-bearing selectors (testid, attribute, aria/name) score high; a
 * positional css path or an xpath scores low so it is heal-first. */
function selectorConfidence(sel: { kind: string; value: string }, ambiguous: boolean): number {
  let c: number;
  switch (sel.kind) {
    case 'aria':
      c = 0.86;
      break;
    case 'text':
      c = 0.74;
      break;
    case 'xpath':
      c = 0.55;
      break;
    case 'pierce':
      c = 0.7;
      break;
    default: // css
      if (isTestIdSelector(sel.value)) c = 0.97;
      else if (isPositionalSelector(sel.value)) c = 0.6;
      else if (isAttributeSelector(sel.value)) c = 0.93;
      else c = 0.74; // container-anchored / class path
  }
  // a non-unique selector we had to rank among is a touch less certain
  return ambiguous ? Math.min(c, 0.72) : c;
}

/** Actuation families that must never be swapped: activating a control (button,
 * menuitem, tab, …) does something in place; following a link navigates away.
 * Binding a "click the Save button" step onto a look-alike link would silently
 * leave the page — so a family conflict blocks the match and it heals instead.
 * Returns '' when the family is unknown. */
export function familyOfRole(role: string | undefined): 'activate' | 'navigate' | '' {
  const r = (role || '').toLowerCase();
  if (r === 'link') return 'navigate';
  if (['button', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab', 'switch', 'checkbox', 'radio', 'option'].includes(r)) return 'activate';
  return '';
}

/** True when the recorded role and the candidate element are in opposite
 * actuation families (activate ↔ navigate) — never bind across them. */
export function roleFamilyConflict(el: Element, target: Target): boolean {
  const recorded = familyOfRole(target.aria?.role);
  const got = familyOfRole(implicitRole(el));
  return recorded !== '' && got !== '' && recorded !== got;
}

/** Sanity check before trusting a selector hit (a wrong cached click is worse
 * than a slow click). Strictness depends on the selector kind: semantic
 * selectors (testid/aria/text) carry their own evidence — a unique hit with
 * agreeing role/tag is trustworthy even when the fingerprint drifted;
 * structural selectors (css path/xpath) prove nothing about identity, so they
 * must agree with the recorded fingerprint/name/text. */
function verify(el: Element, target: Target, kind: string, unique: boolean, value?: string): boolean {
  // Never actuate across families — a "click button" step must not bind onto a
  // link (it would navigate away). Heal instead. Checked before everything else.
  if (roleFamilyConflict(el, target)) return false;

  const fp = target.fingerprint;
  const wantTag = fp?.tagPath.split('/').pop();
  const tagMatches = !wantTag || el.tagName.toLowerCase() === wantTag;

  if (isTestIdSelector(value)) return tagMatches; // testids are app contracts

  // A UNIQUE hit from an intentional attribute selector (img[alt^="Generated
  // image"], [name="q"], input[type="email"]) carries its own evidence — trust
  // it even when the fingerprint drifted. This is the whole point of a
  // generalizing selector for content-varying elements; a positional css path
  // (with combinators) is NOT trusted this way and still verifies below.
  if (kind === 'css' && unique && value && isAttributeSelector(value) && tagMatches) return true;

  if (kind === 'aria') {
    // the query already matched role + accessible name
    return tagMatches || scoreCandidate(el, target) >= 0.45;
  }

  if (kind === 'text') {
    const wantRole = target.aria?.role;
    const curRole = implicitRole(el);
    if (wantRole && curRole === wantRole && (unique || tagMatches)) return true;
    return scoreCandidate(el, target) >= 0.45;
  }

  // structural kinds: css / xpath / pierce
  if (fp) {
    const cur = computeFingerprint(el);
    if (cur.stableHash === fp.stableHash) return true;
    if (!tagMatches) return false;
  }
  const wantName = normText(target.aria?.name ?? '').toLowerCase();
  if (wantName && normText(axName(el)).toLowerCase() === wantName) return true;
  const wantText = normText(target.text?.content ?? '').toLowerCase();
  if (wantText) {
    const curText = visibleText(el, 200).toLowerCase();
    if (target.text?.exact ? curText === wantText : curText.includes(wantText)) return true;
  }
  if (!wantName && !wantText && !fp) return true;
  return scoreCandidate(el, target) >= 0.6;
}

/** How well does `el` match the recorded target? 0..1 — the L2 ranking function,
 * also used to rank among several hits of one non-unique selector. */
export function scoreCandidate(el: Element, target: Target): number {
  // Cross-family candidates (a link for a recorded button, or vice-versa) are
  // never the right actuation target — score them out so L2 can't pick one.
  if (roleFamilyConflict(el, target)) return 0;

  let score = 0;

  // role + name (.35)
  const wantRole = target.aria?.role;
  const wantName = normText(target.aria?.name ?? '').toLowerCase();
  const curRole = implicitRole(el);
  const curName = normText(axName(el)).toLowerCase();
  let rn = 0;
  if (wantRole && curRole === wantRole) rn += 0.4;
  if (wantName) {
    if (curName === wantName) rn += 0.6;
    else if (curName) {
      // Graded (Similo): a substring floor, else normalized string similarity —
      // "Create secret key" vs "Create new secret key" degrades smoothly instead
      // of snapping to zero the way a binary equals would.
      const sub = curName.includes(wantName) || wantName.includes(curName) ? 0.5 : 0;
      rn += 0.6 * Math.max(sub, strSim(wantName, curName));
    }
  }
  score += 0.35 * Math.min(1, rn / (wantRole && wantName ? 1 : wantRole || wantName ? 0.6 : 1) || 0);

  // text (.25)
  const wantText = normText(target.text?.content ?? '').toLowerCase();
  if (wantText) {
    const curText = visibleText(el, 200).toLowerCase();
    if (curText === wantText) score += 0.25;
    else if (curText.includes(wantText) || wantText.includes(curText.slice(0, 40))) score += 0.15;
    else score += 0.25 * Math.max(tokenOverlap(wantText, curText), strSim(wantText.slice(0, 80), curText.slice(0, 80)));
  }

  const fp = target.fingerprint;
  if (fp) {
    // attrs (.20) — graded per key (Similo/Healenium): a near-miss attribute
    // ("submit-btn" → "submit-btn-v2", a class list that dropped one token) earns
    // partial credit via normalized similarity rather than a binary 0, so a
    // lightly-refactored element still ranks above unrelated ones.
    const cur = computeFingerprint(el);
    const keys = new Set([...Object.keys(fp.attrs), ...Object.keys(cur.attrs)]);
    if (keys.size > 0) {
      let match = 0;
      for (const k of keys) {
        const a = fp.attrs[k];
        const b = cur.attrs[k];
        if (a === undefined || b === undefined) continue;
        match += a === b ? 1 : strSim(a, b);
      }
      score += 0.2 * (match / keys.size);
    }
    // tagPath / ancestry (.15)
    score += 0.15 * pathSimilarity(fp.tagPath, cur.tagPath);
    // geometry (.05)
    if (target.bbox) {
      const r = el.getBoundingClientRect?.();
      if (r && (r.width || r.height)) {
        const dx = Math.abs(r.x - target.bbox.x) + Math.abs(r.y - target.bbox.y);
        score += 0.05 * Math.max(0, 1 - dx / 800);
      }
    }
  }
  return Math.min(1, score + contextBonus(el, target));
}

/** Additive disambiguation from the recorded surroundings — container hint,
 * position hint and neighbour text. It never invents a match on its own (a wrong
 * element in the right section still scores low); it breaks ties between
 * otherwise-similar candidates so the deterministic cascade resolves them.
 * Capped so it can lift a marginal-but-correct candidate over the 0.80 L2 bar
 * and widen the runner-up margin. */
function contextBonus(el: Element, target: Target): number {
  let bonus = 0;
  const wantContainer = normText(target.hints?.container ?? '', 60).toLowerCase();
  if (wantContainer) {
    const curContainer = normText(containerHintOf(el) ?? '', 60).toLowerCase();
    if (curContainer === wantContainer) bonus += 0.1;
    else if (curContainer && (curContainer.includes(wantContainer) || wantContainer.includes(curContainer))) bonus += 0.05;
  }
  const wantNeighbors = target.fingerprint?.neighborText ?? [];
  if (wantNeighbors.length) {
    const cur = new Set(neighborTexts(el).map((t) => normText(t).toLowerCase()));
    const hits = wantNeighbors.filter((t) => cur.has(normText(t).toLowerCase())).length;
    bonus += 0.06 * (hits / wantNeighbors.length);
  }
  const wantPos = target.hints?.position;
  if (wantPos && positionHintOf(el) === wantPos) bonus += 0.03;
  return bonus;
}

/** Pick among same-named candidates using the recorded surroundings. Returns a
 * single element only when one clearly wins (container/position/neighbor
 * evidence), else undefined so the caller falls through to structural scoring. */
function disambiguate(candidates: Element[], target: Target): Element | undefined {
  let best: { el: Element; s: number } | null = null;
  let tie = false;
  for (const el of candidates) {
    const s = contextBonus(el, target);
    if (!best || s > best.s) {
      tie = false;
      best = { el, s };
    } else if (s === best.s) {
      tie = true;
    }
  }
  // require a positive, unambiguous winner
  return best && best.s > 0 && !tie ? best.el : undefined;
}

function tokenOverlap(a: string, b: string): number {
  const ta = new Set(a.split(/\W+/).filter((w) => w.length > 2));
  const tb = new Set(b.split(/\W+/).filter((w) => w.length > 2));
  if (ta.size === 0 || tb.size === 0) return 0;
  let hits = 0;
  for (const w of ta) if (tb.has(w)) hits++;
  return hits / ta.size;
}

function pathSimilarity(a: string, b: string): number {
  if (a === b) return 1;
  const pa = a.split('/');
  const pb = b.split('/');
  if (pa[pa.length - 1] !== pb[pb.length - 1]) return 0; // different tag = no credit
  let common = 0;
  for (let i = 0; i < Math.min(pa.length, pb.length); i++) {
    if (pa[pa.length - 1 - i] === pb[pb.length - 1 - i]) common++;
    else break;
  }
  return common / Math.max(pa.length, pb.length);
}
