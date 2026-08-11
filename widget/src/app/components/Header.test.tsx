import { describe, expect, it } from 'vitest'

import { needsHeaderClose } from './Header'

describe('needsHeaderClose', () => {
  it('hides the header ✕ in the desktop panel, where the launcher is the ✕', () => {
    // Desktop: 400px panel iframe on a wide screen.
    expect(needsHeaderClose(400, 1920)).toBe(false)
  })

  it('shows it on phones (fullscreen iframe, launcher faded out)', () => {
    expect(needsHeaderClose(390, 390)).toBe(true)
    expect(needsHeaderClose(414, 414)).toBe(true)
  })

  it('shows it in narrow desktop windows that trigger the fullscreen breakpoint', () => {
    // Host window ≤480px on a big screen: loader goes fullscreen + hides the
    // launcher, so the header must carry the close affordance.
    expect(needsHeaderClose(450, 1920)).toBe(true)
    expect(needsHeaderClose(380, 1920)).toBe(true)
  })

  it('fails safe (shows the ✕) when the screen size is unknown', () => {
    expect(needsHeaderClose(1024, 0)).toBe(true)
  })
})
