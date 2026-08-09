import { describe, expect, it, vi } from 'vitest'

import {
  ActionRegistry,
  HANDLER_TIMEOUT_MS,
  MAX_RESULT_CHARS,
  serializeResult,
} from './actions'

function silenced<T>(fn: () => T): T {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  try {
    return fn()
  } finally {
    warn.mockRestore()
  }
}

describe('ActionRegistry.register', () => {
  it('accepts a valid def and advertises it function-free with defaults', () => {
    const registry = new ActionRegistry()
    expect(
      registry.register({
        name: 'invite_teammate',
        description: 'Invite a teammate',
        params: { type: 'object', properties: { email: { type: 'string' } } },
        run: () => 'ok',
      }),
    ).toBe(true)
    expect(registry.wireDefs()).toEqual([
      {
        name: 'invite_teammate',
        description: 'Invite a teammate',
        params: { type: 'object', properties: { email: { type: 'string' } } },
        confirm: true,
        approval: false,
        requires_identity: false,
      },
    ])
  })

  it('rejects bad names, missing descriptions, and missing handlers', () => {
    const registry = new ActionRegistry()
    silenced(() => {
      expect(registry.register({ name: 'Bad-Name', description: 'x', run: () => 0 })).toBe(false)
      expect(registry.register({ name: 'ok', description: '  ', run: () => 0 })).toBe(false)
      expect(registry.register({ name: 'ok', description: 'x' })).toBe(false)
      expect(registry.register('nope')).toBe(false)
    })
    expect(registry.size()).toBe(0)
  })

  it('re-registering a name replaces the def and its position', () => {
    const registry = new ActionRegistry()
    registry.register({ name: 'a', description: 'first a', run: () => 0 })
    registry.register({ name: 'b', description: 'b', run: () => 0 })
    registry.register({ name: 'a', description: 'second a', confirm: false, run: () => 0 })
    expect(registry.wireDefs().map((d) => d.name)).toEqual(['b', 'a'])
    expect(registry.wireDefs()[1]).toMatchObject({ description: 'second a', confirm: false })
  })

  it('remove() reports whether anything was removed', () => {
    const registry = new ActionRegistry()
    registry.register({ name: 'a', description: 'a', run: () => 0 })
    expect(registry.remove('a')).toBe(true)
    expect(registry.remove('a')).toBe(false)
  })
})

describe('ActionRegistry.execute', () => {
  it('runs the handler with the params and wraps the result', async () => {
    const registry = new ActionRegistry()
    const run = vi.fn().mockResolvedValue({ invited: true })
    registry.register({ name: 'invite', description: 'x', run })
    const result = await registry.execute('invite', { email: 'sam@acme.io' })
    expect(run).toHaveBeenCalledWith({ email: 'sam@acme.io' })
    expect(result).toEqual({ ok: true, result: '{"invited":true}' })
  })

  it('answers not-available for an unregistered name', async () => {
    const result = await new ActionRegistry().execute('ghost', {})
    expect(result.ok).toBe(false)
    expect(String(result.error)).toContain("'ghost' is not available on this page")
  })

  it('turns a throwing handler into an error result, never a throw', async () => {
    const registry = new ActionRegistry()
    registry.register({
      name: 'boom',
      description: 'x',
      run: () => {
        throw new Error('nope')
      },
    })
    await expect(registry.execute('boom', {})).resolves.toEqual({ ok: false, error: 'nope' })
  })

  it('times out a hanging handler', async () => {
    vi.useFakeTimers()
    try {
      const registry = new ActionRegistry()
      registry.register({ name: 'hang', description: 'x', run: () => new Promise(() => {}) })
      const pending = registry.execute('hang', {})
      await vi.advanceTimersByTimeAsync(HANDLER_TIMEOUT_MS + 1)
      const result = await pending
      expect(result.ok).toBe(false)
      expect(String(result.error)).toContain('timed out')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('serializeResult', () => {
  it('keeps strings, JSON-encodes values, and defaults undefined to done', () => {
    expect(serializeResult('plain')).toBe('plain')
    expect(serializeResult({ a: 1 })).toBe('{"a":1}')
    expect(serializeResult(undefined)).toBe('done')
    expect(serializeResult(null)).toBe('done')
  })

  it('caps oversized results', () => {
    expect(serializeResult('x'.repeat(MAX_RESULT_CHARS * 2))).toHaveLength(MAX_RESULT_CHARS)
  })

  it('never throws on circular values', () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular
    expect(typeof serializeResult(circular)).toBe('string')
  })
})
