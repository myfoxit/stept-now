import { describe, expect, it } from 'vitest'

import {
  describeTrigger,
  emptyOption,
  emptyQuestion,
  formatRate,
  isoToLocalInput,
  localInputToIso,
  moveQuestion,
  npsSegments,
  ratingRows,
  selectRows,
  serializeFilters,
  serializeQuestion,
  toQuestionDraft,
  validateQuestions,
  type SurveyQuestionDraft,
} from './lib'

function draft(overrides: Partial<SurveyQuestionDraft> = {}): SurveyQuestionDraft {
  return { ...emptyQuestion(), question: 'How are we doing?', ...overrides }
}

describe('serializeQuestion', () => {
  it('emits options for select questions only', () => {
    const select = serializeQuestion(
      draft({ type: 'select', options: [emptyOption(' Support '), emptyOption('Onboarding')] })
    )
    expect(select).toEqual({
      type: 'select',
      question: 'How are we doing?',
      required: true,
      options: ['Support', 'Onboarding'],
    })

    // A non-select question never carries options — the backend rejects them.
    const nps = serializeQuestion(
      draft({ type: 'nps', options: [emptyOption('a'), emptyOption('b')] })
    )
    expect(nps).toEqual({ type: 'nps', question: 'How are we doing?', required: true })
    expect('options' in nps).toBe(false)
  })

  it('drops blank options and keeps ids for saved questions', () => {
    expect(
      serializeQuestion(
        draft({ id: 'q9', type: 'select', options: [emptyOption('Yes'), emptyOption('  ')] })
      )
    ).toEqual({ id: 'q9', type: 'select', question: 'How are we doing?', required: true, options: ['Yes'] })
    expect(serializeQuestion(draft({ id: null })).id).toBeUndefined()
  })

  it('round-trips a stored select question through the draft', () => {
    const hydrated = toQuestionDraft({
      id: 'q3',
      type: 'select',
      question: 'What do you use Stept for?',
      required: false,
      options: ['Support', 'Onboarding', 'Both'],
    })
    expect(hydrated.options.map((o) => o.value)).toEqual(['Support', 'Onboarding', 'Both'])
    expect(serializeQuestion(hydrated)).toEqual({
      id: 'q3',
      type: 'select',
      question: 'What do you use Stept for?',
      required: false,
      options: ['Support', 'Onboarding', 'Both'],
    })
  })
})

describe('validateQuestions', () => {
  it('enforces text, option counts, uniqueness and the question cap', () => {
    expect(validateQuestions([draft({ type: 'nps' })])).toBeNull()
    expect(validateQuestions([draft({ question: '  ' })])).toMatch(/Question 1 needs some text/)

    expect(validateQuestions([draft({ type: 'select', options: [emptyOption('only')] })])).toMatch(
      /between 2 and 6 options/
    )
    expect(
      validateQuestions([
        draft({
          type: 'select',
          options: ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((v) => emptyOption(v)),
        }),
      ])
    ).toMatch(/between 2 and 6 options/)
    expect(
      validateQuestions([
        draft({ type: 'select', options: [emptyOption('Same'), emptyOption('Same')] }),
      ])
    ).toMatch(/duplicate options/)

    expect(validateQuestions(Array.from({ length: 11 }, () => draft()))).toMatch(
      /at most 10 questions/
    )
  })
})

describe('moveQuestion', () => {
  it('reorders immutably and ignores out-of-range moves', () => {
    const items = ['a', 'b', 'c']
    expect(moveQuestion(items, 2, 1)).toEqual(['a', 'c', 'b'])
    expect(items).toEqual(['a', 'b', 'c'])
    expect(moveQuestion(items, 0, -1)).toBe(items)
    expect(moveQuestion(items, 1, 3)).toBe(items)
  })
})

describe('schedule conversion', () => {
  it('round-trips between ISO instants and datetime-local values', () => {
    const local = isoToLocalInput('2026-08-01T09:30:00Z')
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(localInputToIso(local)).toBe('2026-08-01T09:30:00.000Z')
  })

  it('treats blank and invalid values as "no bound"', () => {
    expect(isoToLocalInput(null)).toBe('')
    expect(isoToLocalInput(undefined)).toBe('')
    expect(isoToLocalInput('nonsense')).toBe('')
    expect(localInputToIso('')).toBeNull()
    expect(localInputToIso('nonsense')).toBeNull()
  })
})

describe('nps math', () => {
  it('derives segment shares from the classified responses', () => {
    const { total, segments } = npsSegments({
      score: 40,
      promoters: 10,
      passives: 5,
      detractors: 5,
    })
    expect(total).toBe(20)
    expect(segments.map((s) => [s.key, s.count, s.percent])).toEqual([
      ['promoters', 10, 50],
      ['passives', 5, 25],
      ['detractors', 5, 25],
    ])
  })

  it('handles an empty result without dividing by zero', () => {
    const { total, segments } = npsSegments({ score: 0, promoters: 0, passives: 0, detractors: 0 })
    expect(total).toBe(0)
    expect(segments.every((s) => s.percent === 0)).toBe(true)
  })

  it('rounds shares to one decimal', () => {
    const { segments } = npsSegments({ score: 33, promoters: 2, passives: 1, detractors: 0 })
    expect(segments[0]!.percent).toBe(66.7)
    expect(segments[1]!.percent).toBe(33.3)
  })
})

describe('result row helpers', () => {
  it('fills missing rating buckets with zero and keeps 1..5 order', () => {
    expect(ratingRows({ '5': 3, '1': 1 })).toEqual([
      { rating: '1', count: 1 },
      { rating: '2', count: 0 },
      { rating: '3', count: 0 },
      { rating: '4', count: 0 },
      { rating: '5', count: 3 },
    ])
  })

  it('sorts select options by count desc then label', () => {
    expect(selectRows({ Beta: 2, Alpha: 2, Gamma: 5 })).toEqual([
      { option: 'Gamma', count: 5 },
      { option: 'Alpha', count: 2 },
      { option: 'Beta', count: 2 },
    ])
  })
})

describe('audience filters', () => {
  it('serializes rows, expands attribute keys and drops incomplete rows', () => {
    expect(
      serializeFilters([
        { key: 'a', field: 'attributes', attrKey: 'plan', op: 'eq', value: 'pro' },
        { key: 'b', field: 'email', attrKey: '', op: 'not_exists', value: '' },
        { key: 'c', field: 'name', attrKey: '', op: 'contains', value: '  ' },
      ])
    ).toEqual([
      { field: 'attributes.plan', op: 'eq', value: 'pro' },
      { field: 'email', op: 'not_exists' },
    ])
  })
})

describe('display helpers', () => {
  it('describes the trigger and formats rates', () => {
    expect(describeTrigger({ trigger: { type: 'manual' } })).toBe('Manual')
    expect(describeTrigger({ trigger: { type: 'url_match', url_pattern: '*/inbox*' } })).toBe(
      'On */inbox*'
    )
    expect(formatRate(0.75)).toBe('75%')
    expect(formatRate(undefined)).toBe('—')
  })
})
