import { describe, expect, it } from 'vitest'

import {
  coerceFilterValue,
  describeTrigger,
  emptyItem,
  formatRate,
  moveItem,
  serializeFilters,
  serializeItem,
  toFilterDraft,
  toItemDraft,
  validateItems,
  type ChecklistItemDraft,
} from './lib'

function draft(overrides: Partial<ChecklistItemDraft> = {}): ChecklistItemDraft {
  return { ...emptyItem(), title: 'Connect an inbox', ...overrides }
}

describe('serializeItem', () => {
  it('emits only the fields the selected action union member allows', () => {
    const withTour = serializeItem(
      draft({ actionType: 'start_tour', actionTourId: 't1', actionUrl: 'https://leftover' })
    )
    expect(withTour.action).toEqual({ type: 'start_tour', tour_id: 't1' })

    const withUrl = serializeItem(
      draft({ actionType: 'open_url', actionUrl: '  https://docs.example.com  ', actionTourId: 't1' })
    )
    expect(withUrl.action).toEqual({ type: 'open_url', url: 'https://docs.example.com' })

    expect(serializeItem(draft({ actionType: 'open_messenger', actionTourId: 't1' })).action).toEqual(
      { type: 'open_messenger' }
    )
    expect(serializeItem(draft({ actionType: 'none' })).action).toEqual({ type: 'none' })
  })

  it('emits only the fields the selected completion union member allows', () => {
    expect(
      serializeItem(draft({ completionType: 'tour_completed', completionTourId: 't2' })).completion
    ).toEqual({ type: 'tour_completed', tour_id: 't2' })

    expect(
      serializeItem(
        draft({
          completionType: 'url_visited',
          completionUrlPattern: ' */settings* ',
          completionTourId: 't2',
        })
      ).completion
    ).toEqual({ type: 'url_visited', url_pattern: '*/settings*' })

    expect(
      serializeItem(draft({ completionType: 'manual', completionUrlPattern: '*/x*' })).completion
    ).toEqual({ type: 'manual' })
  })

  it('omits id for new items and keeps it for saved ones, trimming the title', () => {
    expect(serializeItem(draft({ id: null, title: '  Invite a teammate  ' }))).toMatchObject({
      title: 'Invite a teammate',
    })
    expect(serializeItem(draft({ id: null })).id).toBeUndefined()
    expect(serializeItem(draft({ id: 'saved-1' })).id).toBe('saved-1')
  })
})

describe('toItemDraft', () => {
  it('hydrates conditional fields from a stored item', () => {
    const hydrated = toItemDraft({
      id: 'i9',
      title: 'Take the tour',
      body: 'md',
      action: { type: 'start_tour', tour_id: 't1' },
      completion: { type: 'url_visited', url_pattern: '*/settings*' },
    })
    expect(hydrated).toMatchObject({
      id: 'i9',
      actionType: 'start_tour',
      actionTourId: 't1',
      actionUrl: '',
      completionType: 'url_visited',
      completionUrlPattern: '*/settings*',
      completionTourId: '',
    })
    // A round trip through the serializer reproduces the wire shape.
    expect(serializeItem(hydrated).action).toEqual({ type: 'start_tour', tour_id: 't1' })
  })
})

describe('moveItem', () => {
  it('reorders immutably and ignores out-of-range moves', () => {
    const items = ['a', 'b', 'c']
    expect(moveItem(items, 0, 1)).toEqual(['b', 'a', 'c'])
    expect(moveItem(items, 2, 0)).toEqual(['c', 'a', 'b'])
    expect(items).toEqual(['a', 'b', 'c'])
    expect(moveItem(items, 0, -1)).toBe(items)
    expect(moveItem(items, 2, 3)).toBe(items)
    expect(moveItem(items, 1, 1)).toBe(items)
  })
})

describe('validateItems', () => {
  it('flags the first missing conditional field', () => {
    expect(validateItems([draft()])).toBeNull()
    expect(validateItems([draft({ title: '  ' })])).toMatch(/Item 1 needs a title/)
    expect(validateItems([draft(), draft({ actionType: 'start_tour' })])).toMatch(/Item 2/)
    expect(validateItems([draft({ actionType: 'open_url' })])).toMatch(/needs a URL/)
    expect(validateItems([draft({ completionType: 'tour_completed' })])).toMatch(/completes it/)
    expect(validateItems([draft({ completionType: 'url_visited' })])).toMatch(/URL pattern/)
  })

  it('rejects more than 20 items', () => {
    const many = Array.from({ length: 21 }, () => draft())
    expect(validateItems(many)).toMatch(/at most 20 items/)
  })
})

describe('audience filters', () => {
  it('serializes rows, expands attribute keys and drops incomplete rows', () => {
    const rows = [
      { key: 'a', field: 'email', attrKey: '', op: 'contains' as const, value: '@acme.co' },
      { key: 'b', field: 'attributes', attrKey: 'plan', op: 'eq' as const, value: 'pro' },
      { key: 'c', field: 'verified', attrKey: '', op: 'eq' as const, value: 'true' },
      { key: 'd', field: 'name', attrKey: '', op: 'exists' as const, value: '' },
      { key: 'e', field: 'email', attrKey: '', op: 'eq' as const, value: '   ' },
      { key: 'f', field: 'attributes', attrKey: '  ', op: 'eq' as const, value: 'x' },
    ]
    expect(serializeFilters(rows)).toEqual([
      { field: 'email', op: 'contains', value: '@acme.co' },
      { field: 'attributes.plan', op: 'eq', value: 'pro' },
      { field: 'verified', op: 'eq', value: true },
      { field: 'name', op: 'exists' },
    ])
  })

  it('round-trips a stored attribute filter into a draft row', () => {
    expect(toFilterDraft({ field: 'attributes.plan', op: 'eq', value: 'pro' })).toMatchObject({
      field: 'attributes',
      attrKey: 'plan',
      op: 'eq',
      value: 'pro',
    })
    expect(toFilterDraft({ field: 'name', op: 'not_exists' })).toMatchObject({
      field: 'name',
      attrKey: '',
      value: '',
    })
  })

  it('coerces only booleans', () => {
    expect(coerceFilterValue('true')).toBe(true)
    expect(coerceFilterValue(' false ')).toBe(false)
    expect(coerceFilterValue('12345')).toBe('12345')
    expect(coerceFilterValue(' pro ')).toBe('pro')
  })
})

describe('display helpers', () => {
  it('describes the trigger', () => {
    expect(describeTrigger({ trigger: { type: 'manual' } })).toBe('Manual')
    expect(describeTrigger({ trigger: { type: 'url_match', url_pattern: '/app*' } })).toBe('On /app*')
    expect(describeTrigger({ trigger: { type: 'url_match', url_pattern: '' } })).toBe(
      'On every page'
    )
  })

  it('formats rates', () => {
    expect(formatRate(0.732)).toBe('73%')
    expect(formatRate(0)).toBe('0%')
    expect(formatRate(null)).toBe('—')
  })
})
