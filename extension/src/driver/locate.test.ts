import { describe, expect, it } from 'vitest';
import {
  ACTUATE_CONFIDENCE,
  locate,
  LOCATE_POLL_MS,
  STRONG_CONFIDENCE,
  WEAK_HOLD_MS,
  type LocateHit,
} from './locate';

/** A fake executor + a fake clock: the locate loop's decisions are pure, so
 * the whole confidence-banking contract is testable without a browser. */
function harness(script: Array<LocateHit | null>) {
  let now = 0;
  let calls = 0;
  return {
    deps: {
      resolve: async () => {
        const next = script[Math.min(calls, script.length - 1)] ?? null;
        calls += 1;
        return next;
      },
      now: () => now,
      // "sleeping" just advances the fake clock — no real timers
      sleep: async (ms: number) => {
        now += ms;
      },
    },
    get calls() {
      return calls;
    },
    advance: (ms: number) => {
      now += ms;
    },
  };
}

const hit = (confidence: number, over: Partial<LocateHit> = {}): LocateHit => ({
  x: 10,
  y: 20,
  confidence,
  healed: false,
  via: 'primary',
  detail: 'L0',
  ...over,
});

describe('locate', () => {
  it('commits immediately on a strong hit — no added latency', async () => {
    const h = harness([hit(STRONG_CONFIDENCE)]);
    const out = await locate(h.deps, 5_000);
    expect(out).toMatchObject({ ok: true, weak: false, tries: 1 });
  });

  it('banks an ambiguous hit and polls for a stronger one before committing', async () => {
    // first two polls are ambiguous, the third is the real primary appearing
    const h = harness([hit(0.71), hit(0.71), hit(0.95)]);
    const out = await locate(h.deps, 5_000);
    expect(out.ok).toBe(true);
    if (out.ok) {
      expect(out.hit.confidence).toBe(0.95);
      expect(out.weak).toBe(false);
      expect(out.tries).toBe(3);
    }
  });

  it('commits the best weak hit once the hold window elapses', async () => {
    const h = harness([hit(ACTUATE_CONFIDENCE), hit(0.72), hit(0.72)]);
    const out = await locate(h.deps, 30_000);
    expect(out.ok).toBe(true);
    if (out.ok) {
      expect(out.weak).toBe(true);
      expect(out.hit.confidence).toBe(0.72); // the BEST weak hit, not the first
      // one poll interval per attempt until WEAK_HOLD_MS is covered
      expect(out.tries).toBeLessThanOrEqual(Math.ceil(WEAK_HOLD_MS / LOCATE_POLL_MS) + 1);
    }
  });

  it('never commits a brittle sub-actuate hit early — it waits out the deadline', async () => {
    const h = harness([hit(0.4)]);
    const out = await locate(h.deps, 1_000);
    expect(out.ok).toBe(true);
    if (out.ok) {
      expect(out.weak).toBe(true);
      expect(out.hit.confidence).toBe(0.4);
      expect(out.tries).toBeGreaterThan(1); // it kept polling for something better
    }
  });

  it('prefers a late strong hit over a banked brittle one', async () => {
    const script = [hit(0.4), hit(0.4), hit(0.99)];
    const h = harness(script);
    const out = await locate(h.deps, 5_000);
    expect(out.ok && out.hit.confidence).toBe(0.99);
  });

  it('times out when nothing ever resolves', async () => {
    const h = harness([null]);
    const out = await locate(h.deps, 1_000);
    expect(out).toMatchObject({ ok: false, reason: 'timeout' });
    expect(out.tries).toBeGreaterThan(1);
  });

  it('bails out immediately when the run is cancelled', async () => {
    const h = harness([null]);
    const out = await locate({ ...h.deps, cancelled: () => true }, 5_000);
    expect(out).toMatchObject({ ok: false, reason: 'cancelled', tries: 0 });
  });

  it('surfaces the healed flag and the occlusion hint on the committed hit', async () => {
    const h = harness([hit(0.9, { healed: true, via: 'fingerprint', occluded: true })]);
    const out = await locate(h.deps, 1_000);
    expect(out.ok && out.hit).toMatchObject({ healed: true, via: 'fingerprint', occluded: true });
  });
});
