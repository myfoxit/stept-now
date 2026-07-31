/** URL globbing shared by the compiler (wait-for-url steps), the save sheet's
 * suggested `url_pattern`, and the background's guide/drive auto-advance.
 *
 * Semantics match the backend's Python `fnmatch` for the subset the extension
 * emits: `*` matches any run of characters, `?` matches one. Character classes
 * (`[seq]`) are backend-only and deliberately NOT supported here — we only ever
 * GENERATE patterns, and never generate a class.
 */

/** Browser-internal / non-navigable URLs that must never become steps: the
 * new-tab page, settings, extension pages, blank/redirect placeholders.
 * Ported from the old compiler's `isInternalUrl`. */
export function isInternalUrl(url: string | undefined): boolean {
  if (!url) return true;
  return (
    /^(chrome|edge|about|chrome-extension|moz-extension|chrome-search|chrome-native|brave|opera|vivaldi|view-source|devtools|data|blob|javascript):/i.test(
      url,
    ) || url === 'about:blank'
  );
}

/** Chrome-style wildcard pattern → RegExp test, case-insensitive (matching the
 * widget's `globMatch` after the wave-7 unification). */
export function wildcardMatch(pattern: string, value: string): boolean {
  if (!pattern) return false;
  const rx = new RegExp(
    '^' +
      pattern
        .split('*')
        .map((chunk) =>
          chunk
            .split('?')
            .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
            .join('.'),
        )
        .join('.*') +
      '$',
    'i',
  );
  return rx.test(value);
}

/** Pattern for "this page, whatever comes after it" — used as a wait step's
 * `url_pattern` and as the effect pattern for a post-click navigation.
 * Wildcards trailing ids so `/d/12345/edit` generalizes to `/d/*` /edit.
 * Ported from the old compiler's `urlPattern`. */
export function urlPatternOf(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/[0-9a-f-]{6,}(?=\/|$)/gi, '/*');
    // never emit `**`: a trailing wildcarded id already covers everything after it
    return path.endsWith('*') ? `${u.origin}${path}` : `${u.origin}${path}*`;
  } catch {
    return url;
  }
}

/**
 * Suggest a tour `url_pattern` from the page the recording started on. Returns
 * an empty string for an unparseable URL (the tour then stays manual-trigger).
 * Mined from the pre-port extension's `payload.ts`, widened to drop a trailing
 * `index`-ish filename and to keep query strings out of the pattern.
 */
export function suggestUrlPattern(href: string): string {
  try {
    const url = new URL(href);
    if (!/^https?:$/.test(url.protocol)) return '';
    const path = url.pathname.replace(/\/+$/, '');
    return `${url.origin}${path}*`;
  } catch {
    return '';
  }
}
