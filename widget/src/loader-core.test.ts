import { describe, expect, it, vi } from 'vitest'

import {
  campaignDelayMs,
  campaignSeenKey,
  createDispatcher,
  firstDueCampaign,
  globMatch,
  installStept,
  pruneSeenCampaigns,
  readSeenSet,
  selectEligibleCampaigns,
  writeSeenSet,
  type SteptCommandHandlers,
  type SteptFn,
} from './loader-core'
import type { Campaign } from './types'

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

// --- proactive campaigns -----------------------------------------------------

function campaign(id: string, rules: Campaign['trigger_rules'] = {}): Campaign {
  return { id, message: `msg ${id}`, trigger_rules: rules, sender_name: 'Ana' }
}

/** In-memory Storage: the seen-set codec is storage-agnostic by design. */
function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  }
}

describe('globMatch', () => {
  it('matches * wildcards anywhere in the URL', () => {
    expect(globMatch('*/pricing*', 'https://site.test/pricing?plan=pro')).toBe(true)
    expect(globMatch('https://site.test/*', 'https://site.test/docs/install')).toBe(true)
    expect(globMatch('*/pricing*', 'https://site.test/docs')).toBe(false)
  })

  it('requires a full-string match, not a substring', () => {
    expect(globMatch('/pricing', 'https://site.test/pricing')).toBe(false)
    expect(globMatch('https://site.test/pricing', 'https://site.test/pricing')).toBe(true)
  })

  it('escapes regex specials and treats ? as a single wildcard char', () => {
    expect(globMatch('*/a.b', 'https://x.test/a.b')).toBe(true)
    expect(globMatch('*/a.b', 'https://x.test/aXb')).toBe(false)
    expect(globMatch('*/v?', 'https://x.test/v1')).toBe(true)
    expect(globMatch('*/v?', 'https://x.test/v12')).toBe(false)
  })
})

describe('selectEligibleCampaigns', () => {
  const url = 'https://site.test/pricing'

  it('keeps pattern-less campaigns and glob matches, drops the rest', () => {
    const list = [
      campaign('always'),
      campaign('match', { url_pattern: '*/pricing*' }),
      campaign('miss', { url_pattern: '*/checkout*' }),
      campaign('blank', { url_pattern: '   ' }),
    ]
    expect(selectEligibleCampaigns(list, url).map((c) => c.id)).toEqual([
      'always',
      'match',
      'blank',
    ])
  })

  it('excludes campaigns already in the seen set, preserving order', () => {
    const list = [campaign('a'), campaign('b'), campaign('c')]
    expect(selectEligibleCampaigns(list, url, ['b']).map((c) => c.id)).toEqual(['a', 'c'])
    expect(selectEligibleCampaigns(list, url, new Set(['a', 'c'])).map((c) => c.id)).toEqual(['b'])
  })
})

describe('firstDueCampaign', () => {
  it('picks the smallest time_on_page (absent → fires immediately)', () => {
    const slow = campaign('slow', { time_on_page_seconds: 30 })
    const fast = campaign('fast', { time_on_page_seconds: 5 })
    const instant = campaign('instant')
    expect(firstDueCampaign([slow, fast])?.id).toBe('fast')
    expect(firstDueCampaign([slow, fast, instant])?.id).toBe('instant')
    expect(campaignDelayMs(fast)).toBe(5000)
    expect(campaignDelayMs(instant)).toBe(0)
  })

  it('breaks ties by list order and returns null when nothing is eligible', () => {
    const a = campaign('a', { time_on_page_seconds: 10 })
    const b = campaign('b', { time_on_page_seconds: 10 })
    expect(firstDueCampaign([a, b])?.id).toBe('a')
    expect(firstDueCampaign([])).toBeNull()
  })
})

describe('campaign seen-set storage', () => {
  it('round-trips through storage under a widgetKey-namespaced key', () => {
    const storage = memoryStorage()
    const key = campaignSeenKey('wk_a')
    expect(key).toBe('stept:wk_a:campaigns')
    writeSeenSet(storage, key, ['c1', 'c2'])
    expect(storage.getItem(key)).toBe('["c1","c2"]')
    expect(readSeenSet(storage, key)).toEqual(new Set(['c1', 'c2']))
    // A different widget key sees an empty set — no cross-widget bleed.
    expect(readSeenSet(storage, campaignSeenKey('wk_b')).size).toBe(0)
  })

  it('tolerates corrupt JSON, junk entries, and missing/broken storage', () => {
    const storage = memoryStorage()
    storage.setItem(campaignSeenKey('wk_x'), '{not json')
    expect(readSeenSet(storage, campaignSeenKey('wk_x')).size).toBe(0)
    storage.setItem(campaignSeenKey('wk_y'), JSON.stringify(['ok', 42, null]))
    expect(readSeenSet(storage, campaignSeenKey('wk_y'))).toEqual(new Set(['ok']))
    expect(readSeenSet(null, 'k').size).toBe(0)
    expect(() => writeSeenSet(null, 'k', ['a'])).not.toThrow()
    const broken = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    }
    expect(readSeenSet(broken, 'k').size).toBe(0)
    expect(() => writeSeenSet(broken, 'k', ['a'])).not.toThrow()
  })

  it('prunes seen ids whose campaigns no longer exist', () => {
    const pruned = pruneSeenCampaigns(['live', 'gone'], [campaign('live'), campaign('other')])
    expect(pruned).toEqual(new Set(['live']))
  })
})
