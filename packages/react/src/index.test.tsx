// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { SteptProvider, useSteptAction } from './index'

interface TestWindow {
  Stept?: ((...args: unknown[]) => void) & { q?: unknown[][] }
  SteptSettings?: unknown
}

const w = window as unknown as TestWindow

afterEach(() => {
  cleanup()
  delete w.Stept
  delete w.SteptSettings
  document.getElementById('stept-loader-script')?.remove()
})

function queued(): unknown[][] {
  return w.Stept?.q ?? []
}

function Invites({ note }: { note: string }) {
  useSteptAction(
    {
      name: 'invite_teammate',
      description: `Invite a teammate (${note})`,
      run: () => note,
    },
    [note],
  )
  return null
}

describe('SteptProvider', () => {
  it('boots the widget on mount with the given settings', () => {
    render(
      <SteptProvider settings={{ workspaceKey: 'wk_1', apiBase: 'https://stept.acme.io' }}>
        <span>app</span>
      </SteptProvider>,
    )
    expect(w.SteptSettings).toMatchObject({ workspaceKey: 'wk_1' })
    expect(document.getElementById('stept-loader-script')).not.toBeNull()
  })
})

describe('useSteptAction', () => {
  it('registers on mount and removes on unmount', () => {
    const view = render(<Invites note="v1" />)
    expect(queued().filter((c) => c[0] === 'action')).toHaveLength(1)

    view.unmount()
    expect(queued().some((c) => c[0] === 'removeAction' && c[1] === 'invite_teammate')).toBe(true)
  })

  it('re-registers when its declared deps change (replace semantics)', () => {
    const view = render(<Invites note="v1" />)
    view.rerender(<Invites note="v2" />)
    const registrations = queued().filter((c) => c[0] === 'action')
    expect(registrations).toHaveLength(2)
    expect((registrations[1]?.[1] as { description: string }).description).toContain('v2')
  })

  it('does not re-register on unrelated re-renders', () => {
    const view = render(<Invites note="v1" />)
    view.rerender(<Invites note="v1" />)
    expect(queued().filter((c) => c[0] === 'action')).toHaveLength(1)
  })
})
