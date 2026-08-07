import { describe, expect, it } from 'vitest'

import { ApiError } from '@/api/client'
import { parseApiError } from '@/lib/errors'

describe('parseApiError', () => {
  it('maps a FastAPI 422 body error onto its field', () => {
    const err = new ApiError(422, 'validation_failed', 'Request validation failed', [
      { type: 'value_error', loc: ['body', 'email'], msg: 'value is not a valid email address' },
    ])
    const parsed = parseApiError(err)
    expect(parsed.fields).toEqual({ email: 'value is not a valid email address' })
    // The generic backend message is replaced by something actionable.
    expect(parsed.message).toBe('email: value is not a valid email address')
  })

  it('keeps only the first message per field', () => {
    const err = new ApiError(422, 'validation_failed', 'Request validation failed', [
      { loc: ['body', 'email'], msg: 'first' },
      { loc: ['body', 'email'], msg: 'second' },
    ])
    expect(parseApiError(err).fields).toEqual({ email: 'first' })
  })

  it('joins nested locations into a dotted path', () => {
    const err = new ApiError(422, 'validation_failed', 'Request validation failed', [
      { loc: ['body', 'attributes', 0, 'key'], msg: 'required' },
    ])
    expect(parseApiError(err).fields).toEqual({ 'attributes.0.key': 'required' })
  })

  it('falls back to the envelope message for non-422 errors', () => {
    const err = new ApiError(403, 'forbidden', 'You cannot edit this contact')
    const parsed = parseApiError(err)
    expect(parsed.fields).toEqual({})
    expect(parsed.message).toBe('You cannot edit this contact')
  })

  it('handles a 422 with no usable details', () => {
    const err = new ApiError(422, 'validation_failed', 'Request validation failed')
    const parsed = parseApiError(err)
    expect(parsed.fields).toEqual({})
    expect(parsed.message).toBe('Request validation failed')
  })

  it('handles non-ApiError throwables', () => {
    expect(parseApiError(new Error('boom')).message).toBe('boom')
    expect(parseApiError('nope').message).toBe('Something went wrong.')
  })
})
