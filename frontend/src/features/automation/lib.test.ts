import { describe, expect, it } from 'vitest'

import {
  deserializeAction,
  deserializeCondition,
  serializeAction,
  serializeCondition,
  type ActionRow,
  type ConditionRow,
} from './lib'

const cond = (over: Partial<ConditionRow>): ConditionRow => ({
  id: 'c1',
  field: 'status',
  op: 'eq',
  value: '',
  ...over,
})

describe('serializeCondition', () => {
  it('coerces scalar values (number/bool) for eq', () => {
    expect(serializeCondition(cond({ op: 'eq', value: 'open' }))).toEqual({
      field: 'status',
      op: 'eq',
      value: 'open',
    })
    expect(serializeCondition(cond({ field: 'priority', op: 'eq', value: '3' })).value).toBe(3)
    expect(serializeCondition(cond({ op: 'eq', value: 'true' })).value).toBe(true)
  })

  it('splits comma-separated lists for the "in" operator', () => {
    expect(serializeCondition(cond({ op: 'in', value: 'open, pending , resolved' }))).toEqual({
      field: 'status',
      op: 'in',
      value: ['open', 'pending', 'resolved'],
    })
  })

  it('drops the operand for "exists"', () => {
    expect(serializeCondition(cond({ op: 'exists', value: 'ignored' }))).toEqual({
      field: 'status',
      op: 'exists',
      value: null,
    })
  })

  it('round-trips through deserialize', () => {
    const wire = serializeCondition(cond({ op: 'in', value: 'a, b' }))
    const row = deserializeCondition(wire)
    expect(row.op).toBe('in')
    expect(row.value).toBe('a, b')
  })
})

describe('serializeAction', () => {
  const row = (over: Partial<ActionRow>): ActionRow => ({
    id: 'a1',
    type: 'set_status',
    params: {},
    ...over,
  })

  it('keeps only non-empty params defined for the action', () => {
    expect(serializeAction(row({ type: 'set_status', params: { status: 'resolved' } }))).toEqual({
      type: 'set_status',
      params: { status: 'resolved' },
    })
  })

  it('omits blank params', () => {
    const result = serializeAction(row({ type: 'notify_member', params: { user_id: 'u1', title: '' } }))
    expect(result.params).toEqual({ user_id: 'u1' })
  })

  it('round-trips through deserialize', () => {
    const wire = serializeAction(row({ type: 'send_reply', params: { content: 'hi' } }))
    const back = deserializeAction(wire)
    expect(back.type).toBe('send_reply')
    expect(back.params.content).toBe('hi')
  })
})
