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
