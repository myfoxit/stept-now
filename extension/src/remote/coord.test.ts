import { describe, expect, it } from 'vitest';
import {
  classifyCoordinateHit,
  cssToScreenshot,
  jpegSize,
  keyDismissedOverlay,
  screenshotToCss,
  type HitInfo,
} from './coord';

describe('screenshotToCss', () => {
  it('maps 1:1 when the screenshot equals the CSS viewport (1× display)', () => {
    const p = screenshotToCss(
      { imgX: 640, imgY: 360 },
      { screenshotW: 1280, screenshotH: 720, viewportW: 1280, viewportH: 720 },
    );
    expect(p).toMatchObject({ x: 640, y: 360, outOfBounds: false });
  });

  it('halves device pixels on a 2× retina capture', () => {
    // 1200×800 CSS viewport captured at device resolution 2400×1600.
    const space = { screenshotW: 2400, screenshotH: 1600, viewportW: 1200, viewportH: 800 };
    expect(screenshotToCss({ imgX: 2400, imgY: 1600 }, space)).toMatchObject({ x: 1200, y: 800 });
    expect(screenshotToCss({ imgX: 1200, imgY: 800 }, space)).toMatchObject({ x: 600, y: 400 });
  });

  it('is correct under page zoom (viewport shrinks, ratio still holds)', () => {
    // 2× page zoom → innerWidth halves to 600; the clipped shot is CSS-sized 600.
    const p = screenshotToCss(
      { imgX: 300, imgY: 150 },
      { screenshotW: 600, screenshotH: 400, viewportW: 600, viewportH: 400 },
    );
    expect(p).toMatchObject({ x: 300, y: 150, outOfBounds: false });
    // and a shot that is still device-sized while zoomed maps by ratio too
    const q = screenshotToCss(
      { imgX: 1200, imgY: 800 },
      { screenshotW: 1200, screenshotH: 800, viewportW: 600, viewportH: 400 },
    );
    expect(q).toMatchObject({ x: 600, y: 400 });
  });

  it('falls back to identity when a dimension is missing (off-focus capture)', () => {
    const p = screenshotToCss(
      { imgX: 42, imgY: 99 },
      { screenshotW: 0, screenshotH: 0, viewportW: 0, viewportH: 0 },
    );
    expect(p).toMatchObject({ x: 42, y: 99, outOfBounds: false });
  });

  it('flags a point that maps outside the viewport', () => {
    const p = screenshotToCss(
      { imgX: 1300, imgY: 100 },
      { screenshotW: 1280, screenshotH: 720, viewportW: 1280, viewportH: 720 },
    );
    expect(p.outOfBounds).toBe(true);
  });
});

describe('cssToScreenshot', () => {
  const space = { screenshotW: 1568, screenshotH: 980, viewportW: 3136, viewportH: 1960 };

  it('is the inverse of screenshotToCss (round-trips a point)', () => {
    const css = screenshotToCss({ imgX: 784, imgY: 490 }, space);
    const img = cssToScreenshot(css, space);
    expect(img.x).toBeCloseTo(784);
    expect(img.y).toBeCloseTo(490);
  });

  it('maps a CSS point into a downscaled screenshot', () => {
    expect(cssToScreenshot({ x: 3136, y: 1960 }, space)).toMatchObject({ x: 1568, y: 980 });
  });

  it('falls back to identity when a dimension is missing', () => {
    expect(
      cssToScreenshot({ x: 7, y: 9 }, { screenshotW: 0, screenshotH: 0, viewportW: 0, viewportH: 0 }),
    ).toMatchObject({ x: 7, y: 9 });
  });
});

describe('classifyCoordinateHit', () => {
  const interactive: HitInfo = { tag: 'button', interactive: true };

  it('ok when the point hit an interactive element', () => {
    expect(classifyCoordinateHit(interactive, false)).toBe('ok');
  });
  it('empty when elementFromPoint returned nothing', () => {
    expect(classifyCoordinateHit(null, false)).toBe('empty');
  });
  it('non-interactive when the point hit dead space / a container', () => {
    expect(classifyCoordinateHit({ tag: 'div', interactive: false }, false)).toBe('non-interactive');
  });
  it('mismatch when out of bounds regardless of the hit', () => {
    expect(classifyCoordinateHit(interactive, true)).toBe('mismatch');
  });
  it('mismatch when an intended element was known and NOT hit', () => {
    expect(classifyCoordinateHit({ tag: 'button', interactive: true, matchesIntended: false }, false)).toBe(
      'mismatch',
    );
  });
});

describe('keyDismissedOverlay', () => {
  it('true only when an open overlay closed after the key', () => {
    expect(keyDismissedOverlay(true, false)).toBe(true); // the key dismissed it
    expect(keyDismissedOverlay(true, true)).toBe(false); // the key did nothing
    expect(keyDismissedOverlay(false, false)).toBe(false); // there was no overlay
  });
});

/** A real 5×3 JPEG (quality 50) — its segment chain carries DQT + DHT (0xFFC4)
 * markers BEFORE the SOF0, so the parser's marker-skipping is actually
 * exercised, not just the happy first-marker path. */
const TINY_JPEG_5X3 =
  '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAADAAUDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDIooorkPqD/9k=';

describe('jpegSize', () => {
  it('reads the true pixel dimensions from the SOF marker', () => {
    expect(jpegSize(TINY_JPEG_5X3)).toEqual({ w: 5, h: 3 });
  });

  it('returns null for base64 that is not a JPEG', () => {
    expect(jpegSize(btoa('hello world, definitely not a jpeg'))).toBeNull();
  });

  it('returns null for a truncated JPEG (SOF never reached)', () => {
    expect(jpegSize(TINY_JPEG_5X3.slice(0, 24))).toBeNull();
  });

  it('returns null for undecodable input', () => {
    expect(jpegSize('!!!not-base64!!!')).toBeNull();
  });
});
