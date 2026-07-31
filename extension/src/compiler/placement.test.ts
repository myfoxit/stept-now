import { describe, expect, it } from 'vitest';
import { placementFor } from './placement';

const vp = { w: 1000, h: 800 };

describe('placementFor', () => {
  it('puts the card below a top-anchored element and above a bottom-anchored one', () => {
    expect(placementFor({ x: 400, y: 8, w: 120, h: 32, viewport: vp })).toBe('bottom');
    expect(placementFor({ x: 400, y: 740, w: 120, h: 32, viewport: vp })).toBe('top');
  });

  it('puts the card opposite a side rail', () => {
    expect(placementFor({ x: 10, y: 400, w: 180, h: 32, viewport: vp })).toBe('right');
    expect(placementFor({ x: 900, y: 400, w: 80, h: 32, viewport: vp })).toBe('left');
  });

  it('leaves comfortably central elements to the player’s own flip logic', () => {
    expect(placementFor({ x: 450, y: 380, w: 100, h: 40, viewport: vp })).toBe('auto');
  });

  it('is auto without geometry', () => {
    expect(placementFor(undefined)).toBe('auto');
    expect(placementFor({ x: 0, y: 0, w: 10, h: 10 })).toBe('auto');
    expect(placementFor({ x: 0, y: 0, w: 10, h: 10, viewport: { w: 0, h: 0 } })).toBe('auto');
  });
});
