import type { BBox } from '@stept/dom-capture';
import type { StepPlacement } from '../types';

/** Where the tooltip should sit relative to its anchor, derived from where the
 * element was on screen at record time (docs/DAP2-CONTRACTS.md §B4: "placement
 * derived from bbox vs viewport quadrant").
 *
 * The rule is "put the card where there is room": an element near the top gets
 * a card BELOW it, one near the bottom gets a card ABOVE, and one hugging a
 * side rail gets a card on the opposite side. Anything comfortably central
 * stays `auto` so the player's own flip logic decides at runtime. Pure — the
 * whole point is that the recorder does not need a live layout to guess well.
 */
export function placementFor(bbox: BBox | undefined | null): StepPlacement {
  const vw = bbox?.viewport?.w ?? 0;
  const vh = bbox?.viewport?.h ?? 0;
  if (!bbox || vw <= 0 || vh <= 0) return 'auto';

  const cx = bbox.x + bbox.w / 2;
  const cy = bbox.y + bbox.h / 2;

  // Vertical edges win: a top nav bar or a bottom action bar is the strongest
  // signal, and the widget's tooltips are wider than they are tall.
  if (cy < vh * 0.28) return 'bottom';
  if (cy > vh * 0.72) return 'top';
  // Side rails: a left nav wants its card to the right and vice versa.
  if (cx < vw * 0.25) return 'right';
  if (cx > vw * 0.75) return 'left';
  return 'auto';
}
