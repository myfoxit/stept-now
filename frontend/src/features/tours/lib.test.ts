import { describe, expect, it } from 'vitest'

import { moveStep, serializeStep, type StepDraft } from './lib'

describe('moveStep', () => {
  const list = ['a', 'b', 'c']

  it('moves an item down', () => {
    expect(moveStep(list, 0, 1)).toEqual(['b', 'a', 'c'])
  })

  it('moves an item up', () => {
    expect(moveStep(list, 2, 1)).toEqual(['a', 'c', 'b'])
  })

  it('is a no-op for out-of-bounds or same index', () => {
    expect(moveStep(list, 0, 0)).toEqual(['a', 'b', 'c'])
    expect(moveStep(list, 0, 3)).toEqual(['a', 'b', 'c'])
    expect(moveStep(list, -1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('does not mutate the input array', () => {
    const original = [...list]
    moveStep(list, 0, 2)
    expect(list).toEqual(original)
  })
})

describe('serializeStep', () => {
  const draft: StepDraft = {
    id: null,
    selector: '  #btn  ',
    title: 'Hi',
    body: 'text',
    placement: 'top',
  }

  it('trims the selector and omits a null id (new step)', () => {
    const out = serializeStep(draft)
    expect(out.selector).toBe('#btn')
    expect('id' in out).toBe(false)
  })

  it('keeps an existing id', () => {
    const out = serializeStep({ ...draft, id: 'step-1' })
    expect(out.id).toBe('step-1')
  })
})
