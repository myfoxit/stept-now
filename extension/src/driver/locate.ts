/**
 * The locate loop — ported from the old `packages/replayer-core/src/runner.ts`
 * `locate()`, minus L3 (there is no server LLM healer in stept-now; see the
 * B4 report). Pure decision logic over an injected executor, which is exactly
 * what makes it unit-testable without a browser.
 *
 * The shape of the problem: a resolve can return a hit that is VERIFIED but
 * brittle — an ambiguous fallback selector that matched a lookalike while the
 * real target was still mounting. Committing to it immediately drives the wrong
 * element; failing outright is worse. So:
 *
 *   confidence ≥ STRONG   → commit at once (no added latency)
 *   confidence ≥ ACTUATE  → bank it, poll WEAK_HOLD_MS for something stronger,
 *                           then commit the best one seen
 *   confidence <  ACTUATE → bank it but keep polling to the deadline; a brittle
 *                           positional handle is the last resort, not the first
 *   nothing               → poll to the deadline, then miss
 */

export const LOCATE_POLL_MS = 250;
/** Below this a verified hit is treated as a GUESS (browser-use's
 * MIN_ACTUATE_CONFIDENCE is 0.6; ours is higher because our low tiers are). */
export const ACTUATE_CONFIDENCE = 0.7;
/** At/above this a hit is a unique, identity-bearing match. */
export const STRONG_CONFIDENCE = 0.73;
/** How long to hold a weak hit while polling for a stronger one. */
export const WEAK_HOLD_MS = 1_500;

export interface LocateHit {
  /** viewport CSS-pixel click point */
  x: number;
  y: number;
  confidence: number;
  healed: boolean;
  via: string;
  detail: string;
  contentEditable?: boolean;
  occluded?: boolean;
}

export interface LocateDeps {
  /** one resolution attempt in the page; null = nothing matched */
  resolve: () => Promise<LocateHit | null>;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  /** stop early (user pressed Stop / paused into a stop) */
  cancelled?: () => boolean;
}

export type LocateOutcome =
  | { ok: true; hit: LocateHit; weak: boolean; tries: number }
  | { ok: false; reason: 'timeout' | 'cancelled'; tries: number };

export async function locate(deps: LocateDeps, timeoutMs: number): Promise<LocateOutcome> {
  const { resolve, now, sleep } = deps;
  const deadline = now() + timeoutMs;
  let bestWeak: LocateHit | null = null;
  // `null`, not 0: with a fake/monotonic clock, `now()` can legitimately BE 0,
  // and a falsy sentinel would restart the hold window on every poll.
  let firstWeakAt: number | null = null;
  let tries = 0;

  for (;;) {
    if (deps.cancelled?.()) return { ok: false, reason: 'cancelled', tries };
    tries += 1;
    const res = await resolve();
    if (res) {
      const conf = res.confidence ?? 1;
      if (conf >= STRONG_CONFIDENCE) return { ok: true, hit: res, weak: false, tries };
      if (conf >= ACTUATE_CONFIDENCE) {
        if (!bestWeak || conf > bestWeak.confidence) bestWeak = res;
        if (firstWeakAt === null) firstWeakAt = now();
        if (now() - firstWeakAt >= WEAK_HOLD_MS || now() >= deadline - LOCATE_POLL_MS) {
          return { ok: true, hit: bestWeak, weak: true, tries };
        }
      } else if (!bestWeak || conf > bestWeak.confidence) {
        // brittle handle: bank it, but never commit before the deadline — the
        // strong primary may still be rendering.
        bestWeak = res;
      }
    }

    if (deps.cancelled?.()) return { ok: false, reason: 'cancelled', tries };
    if (now() >= deadline) {
      // a banked weak hit that never strengthened still beats a hard miss
      if (bestWeak) return { ok: true, hit: bestWeak, weak: true, tries };
      return { ok: false, reason: 'timeout', tries };
    }
    await sleep(LOCATE_POLL_MS);
  }
}
