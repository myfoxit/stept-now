/**
 * Full-page redirect seam for OAuth handoffs. jsdom's `window.location` is
 * [LegacyUnforgeable] — its methods cannot be spied on — so tests mock this
 * module instead to assert that the browser was handed to the authorize URL.
 */
export function assignLocation(url: string): void {
  window.location.assign(url)
}
