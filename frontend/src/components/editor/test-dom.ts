/**
 * jsdom shims for ProseMirror (the engine behind TipTap).
 *
 * ProseMirror measures the document with `Range.getClientRects()` and hit-tests
 * with `document.elementFromPoint()`; jsdom implements neither, so it throws on
 * every transaction. Layout is irrelevant in tests — zero-sized rects and a
 * null hit-test are enough. Call once from a test file that mounts the editor.
 */

const EMPTY_RECT: DOMRect = {
  x: 0,
  y: 0,
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  width: 0,
  height: 0,
  toJSON: () => ({}),
}

export function installEditorDomShims(): void {
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () =>
      Object.assign([], { item: () => null }) as unknown as DOMRectList
  }
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () => EMPTY_RECT
  }
  if (!document.elementFromPoint) {
    Document.prototype.elementFromPoint = () => null
  }
}
