// Wait for the DOM to go quiet — a real "settle" signal instead of a fixed
// sleep. Resolves once no mutations have fired for `quietMs`, or after `maxMs`
// as a hard ceiling. Drive mode waits on this between steps so it never acts
// mid-render (SPA route transitions, async list renders) and never over-waits
// on a quiet page. Ported verbatim from the old extension's `dom-settle.ts`.

export function waitForDomSettle(opts: { quietMs?: number; maxMs?: number } = {}): Promise<void> {
  const quietMs = opts.quietMs ?? 200;
  const maxMs = opts.maxMs ?? 1800;
  return new Promise<void>((resolve) => {
    let quietTimer: ReturnType<typeof setTimeout>;
    let observer: MutationObserver | null = null;
    let done = false;

    const finish = (): void => {
      if (done) return;
      done = true;
      clearTimeout(quietTimer);
      clearTimeout(hardTimer);
      observer?.disconnect();
      resolve();
    };
    const bump = (): void => {
      clearTimeout(quietTimer);
      quietTimer = setTimeout(finish, quietMs);
    };
    const hardTimer = setTimeout(finish, maxMs);

    try {
      const target = document.documentElement || document.body || document;
      observer = new MutationObserver(bump);
      observer.observe(target as Node, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
      });
    } catch {
      // No MutationObserver (exotic env) — fall back to a single quiet window.
    }
    bump(); // start the quiet countdown even if nothing ever mutates
  });
}

/**
 * Full "page is ready to be read" settle, bounded by `maxMs` (~3s default):
 *
 *   1. document.readyState — wait out the initial parse ('loading') so a
 *      snapshot taken right after a tab opens doesn't index an empty document;
 *   2. one rAF tick — a framework that just hydrated has painted at least once;
 *   3. a mutation-quiet window — SPA renders that land AFTER 'complete'
 *      (route transitions, async lists) are what readyState can never see.
 *
 * This is the shared hydration gate: the remote-drive exec island settles with
 * it before extracting, and tour surfaces can reuse it so first-paint anchor
 * resolution stops racing the app's own render.
 */
export async function waitForPageSettled(
  opts: { quietMs?: number; maxMs?: number } = {},
): Promise<void> {
  const maxMs = opts.maxMs ?? 3000;
  const startedAt = Date.now();
  const remaining = (): number => Math.max(0, maxMs - (Date.now() - startedAt));

  if (document.readyState === 'loading') {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, remaining());
      document.addEventListener(
        'DOMContentLoaded',
        () => {
          clearTimeout(timer);
          resolve();
        },
        { once: true },
      );
    });
  }
  await new Promise<void>((resolve) =>
    typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(() => resolve())
      : setTimeout(resolve, 16),
  );
  const quietMs = Math.min(opts.quietMs ?? 200, Math.max(remaining(), 0) || 1);
  await waitForDomSettle({ quietMs, maxMs: Math.max(remaining(), 1) });
}
