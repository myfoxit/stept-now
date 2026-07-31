import { describe, expect, it, vi } from 'vitest'

import { createDispatcher, installStept, type SteptCommandHandlers, type SteptFn } from './loader-core'

function stubHandlers(): SteptCommandHandlers & { calls: string[] } {
  const calls: string[] = []
  return {
    calls,
    boot: () => calls.push('boot'),
    open: () => calls.push('open'),
    close: () => calls.push('close'),
    toggle: () => calls.push('toggle'),
    show: () => calls.push('show'),
    hide: () => calls.push('hide'),
    shutdown: () => calls.push('shutdown'),
    startTour: (id) => calls.push(`startTour:${id}`),
  }
}

describe('createDispatcher', () => {
  it('routes each command to its handler', () => {
    const h = stubHandlers()
    const dispatch = createDispatcher(h)
    dispatch('open')
    dispatch('startTour', 'tour-9')
    expect(h.calls).toEqual(['open', 'startTour:tour-9'])
  })

  it('warns on unknown commands instead of throwing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const dispatch = createDispatcher(stubHandlers())
    expect(() => dispatch('nope')).not.toThrow()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })
})

describe('installStept', () => {
  it('replays queued pre-load calls in order, then installs the real fn', () => {
    const h = stubHandlers()
    // Simulate the queue shim: Stept('boot', …); Stept('open') before load.
    const queued: SteptFn = (() => {}) as SteptFn
    queued.q = [
      ['boot', { workspaceKey: 'wk' }],
      ['open'],
    ]
    const win: { Stept?: SteptFn } = { Stept: queued }

    const dispatch = installStept(win, h)

    expect(h.calls).toEqual(['boot', 'open'])
    expect(win.Stept).toBe(dispatch)

    // Subsequent live calls go straight through.
    win.Stept!('close')
    expect(h.calls).toEqual(['boot', 'open', 'close'])
  })

  it('works when no queue existed', () => {
    const h = stubHandlers()
    const win: { Stept?: SteptFn } = {}
    installStept(win, h)
    expect(h.calls).toEqual([])
    win.Stept!('shutdown')
    expect(h.calls).toEqual(['shutdown'])
  })
})
