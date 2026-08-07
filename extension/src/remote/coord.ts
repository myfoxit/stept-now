/**
 * Remote drive — coordinate calibration + hit verification (pure math).
 *
 * The MCP client picks a click point in SCREENSHOT-pixel space (the JPEG it was
 * shown); trusted CDP `Input.*` wants CSS pixels. The map is a pure per-axis
 * ratio — cssX = imgX · viewportW / screenshotW — correct under BOTH
 * devicePixelRatio (2× retina) and page zoom, unlike dividing by
 * devicePixelRatio (which lands clicks in the wrong place). ALL coordinate acts
 * route through `screenshotToCss` so the mapping cannot drift between ops.
 *
 * Ported from the old repo's `extension/src/coord.ts` +
 * `drive-controller.ts::jpegSize`; no `chrome.*`, no DOM — runs identically in
 * the service worker and in vitest.
 */

export interface ScreenshotSpace {
  screenshotW: number;
  screenshotH: number;
  viewportW: number;
  viewportH: number;
}

export interface CssPoint {
  x: number;
  y: number;
  /** true when the mapped point lands OUTSIDE the CSS viewport — a strong signal
   * the caller's pixel or the calibration is off; the hit check treats it as a
   * miss. */
  outOfBounds: boolean;
}

/** Map a screenshot pixel to a CSS-viewport pixel by the per-axis ratio. Falls
 * back to identity when a dimension is missing (an off-focus capture that
 * returned no size) so a click still fires rather than throwing. */
export function screenshotToCss(img: { imgX: number; imgY: number }, s: ScreenshotSpace): CssPoint {
  const sx = s.screenshotW > 0 && s.viewportW > 0 ? s.viewportW / s.screenshotW : 1;
  const sy = s.screenshotH > 0 && s.viewportH > 0 ? s.viewportH / s.screenshotH : 1;
  const x = img.imgX * sx;
  const y = img.imgY * sy;
  const bounded = s.viewportW > 0 && s.viewportH > 0;
  const outOfBounds = bounded && (x < 0 || y < 0 || x > s.viewportW || y > s.viewportH);
  return { x, y, outOfBounds };
}

/** The inverse map: a CSS-viewport pixel expressed in screenshot pixels — used
 * to annotate results ("clicked at …") in the space the caller thinks in. Same
 * identity fallback as `screenshotToCss` so the pair always round-trips. */
export function cssToScreenshot(css: { x: number; y: number }, s: ScreenshotSpace): { x: number; y: number } {
  const sx = s.screenshotW > 0 && s.viewportW > 0 ? s.screenshotW / s.viewportW : 1;
  const sy = s.screenshotH > 0 && s.viewportH > 0 ? s.screenshotH / s.viewportH : 1;
  return { x: css.x * sx, y: css.y * sy };
}

/** What `document.elementFromPoint(cssX, cssY)` resolved to, described for the
 * post-click hit check. `null` (empty) means the point hit nothing. */
export interface HitInfo {
  tag?: string;
  role?: string;
  name?: string;
  /** the hit element is (or climbs to) an interactive control */
  interactive: boolean;
  /** when an intended target is known (an index click, not a bare coordinate),
   * whether the hit element is / contains / is contained by it. `undefined` for
   * a pure coordinate click where there is no intended element. */
  matchesIntended?: boolean;
}

export type HitVerdict = 'ok' | 'empty' | 'non-interactive' | 'mismatch';

/** Decide whether a coordinate act landed where it should. Advisory only — the
 * caller surfaces a warning on anything but `ok` so a miscalibrated or
 * hallucinated coordinate is never a silent wrong click, but it never blocks
 * the act. */
export function classifyCoordinateHit(hit: HitInfo | null, outOfBounds: boolean): HitVerdict {
  if (outOfBounds) return 'mismatch';
  if (!hit) return 'empty';
  if (hit.matchesIntended === false) return 'mismatch';
  if (!hit.interactive) return 'non-interactive';
  return 'ok';
}

/** Keyboard verification for a focused overlay: after a key press meant to
 * confirm/dismiss it, the key actually worked iff the overlay was open before
 * and is gone after. False while one was open means "the key had no effect" —
 * the caller tells the AI to fall back (click the indexed button, or Escape)
 * instead of assuming success. */
export function keyDismissedOverlay(overlayOpenBefore: boolean, overlayOpenAfter: boolean): boolean {
  return overlayOpenBefore && !overlayOpenAfter;
}

/** Read a JPEG's pixel dimensions from its SOF marker (works in the service
 * worker — no Image/DOM). The screenshot's TRUE pixel size is what calibrates
 * screenshot px → CSS px without trusting devicePixelRatio, and what the MCP
 * snapshot reports as `screenshotSize`. Scans the JPEG segment chain for a
 * Start-Of-Frame marker (0xFFC0–0xFFCF, excluding the non-SOF DHT/JPG/DAC
 * markers) and reads its big-endian 16-bit height/width fields. Returns null on
 * anything that is not parseable JPEG — the caller degrades to computed sizes. */
export function jpegSize(base64: string): { w: number; h: number } | null {
  let bin: string;
  try {
    bin = atob(base64);
  } catch {
    return null;
  }
  const n = bin.length;
  let i = 2; // skip SOI (FFD8)
  while (i + 9 < n) {
    if (bin.charCodeAt(i) !== 0xff) {
      i++;
      continue;
    }
    const marker = bin.charCodeAt(i + 1);
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      const h = (bin.charCodeAt(i + 5) << 8) | bin.charCodeAt(i + 6);
      const w = (bin.charCodeAt(i + 7) << 8) | bin.charCodeAt(i + 8);
      return w && h ? { w, h } : null;
    }
    const len = (bin.charCodeAt(i + 2) << 8) | bin.charCodeAt(i + 3);
    if (len <= 0) break;
    i += 2 + len;
  }
  return null;
}
