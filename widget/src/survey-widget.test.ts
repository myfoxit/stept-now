import { afterEach, describe, expect, it } from 'vitest'

import {
  canAdvance,
  collectAnswers,
  selectFirstEligibleSurvey,
  SurveyWidget,
  surveySeenKey,
} from './survey-widget'
import type { Survey, SurveyAnswer, SurveyQuestion } from './types'

function question(partial: Partial<SurveyQuestion> & { id: string }): SurveyQuestion {
  return { type: 'text', question: partial.id, required: true, ...partial }
}

function surveyOf(questions: SurveyQuestion[], extra: Partial<Survey> = {}): Survey {
  return {
    id: 'sv-1',
    name: 'How are we doing?',
    questions,
    presentation: 'slideout',
    theme: { accent: '#6366f1' },
    thanks_message: 'Thanks for the **feedback**!',
    version: 1,
    ...extra,
  }
}

interface Submission {
  surveyId: string
  answers: SurveyAnswer[]
  completed: boolean
}

let widget: SurveyWidget | null = null

const card = (): HTMLElement | null => document.querySelector('.stept-sv-card')
const cardText = (): string => card()?.textContent ?? ''

afterEach(() => {
  widget?.close(false)
  widget = null
  document.body.innerHTML = ''
  document.getElementById('stept-survey-style')?.remove()
})

describe('survey selection helpers', () => {
  const surveys: Survey[] = [surveyOf([question({ id: 'q1' })]), surveyOf([question({ id: 'q1' })], { id: 'sv-2' })]

  it('namespaces the seen-set per widget key', () => {
    expect(surveySeenKey('wk_a')).toBe('stept:surveys-seen:wk_a')
  })

  it('skips seen surveys unless the frequency is every_time', () => {
    expect(selectFirstEligibleSurvey(surveys, ['sv-1'])?.id).toBe('sv-2')
    expect(selectFirstEligibleSurvey(surveys, ['sv-1', 'sv-2'])).toBeNull()
    const recurring = [{ ...surveys[0]!, frequency_type: 'every_time' }]
    expect(selectFirstEligibleSurvey(recurring, ['sv-1'])?.id).toBe('sv-1')
    expect(selectFirstEligibleSurvey([surveyOf([])], [])).toBeNull()
  })

  it('collects answers in question order, dropping empty ones', () => {
    const questions = [question({ id: 'q1' }), question({ id: 'q2' }), question({ id: 'q3' })]
    const values = new Map<string, number | string>([
      ['q3', 'later'],
      ['q1', 9],
      ['q2', ''],
    ])
    expect(collectAnswers(questions, values)).toEqual([
      { question_id: 'q1', value: 9 },
      { question_id: 'q3', value: 'later' },
    ])
  })

  it('gates advancing on required answers only', () => {
    const required = question({ id: 'q1' })
    const optional = question({ id: 'q2', required: false })
    const values = new Map<string, number | string>()
    expect(canAdvance(required, values)).toBe(false)
    expect(canAdvance(optional, values)).toBe(true)
    values.set('q1', 0)
    expect(canAdvance(required, values)).toBe(true)
  })
})

describe('SurveyWidget', () => {
  it('walks NPS → text and submits the collected answers', () => {
    const submissions: Submission[] = []
    widget = new SurveyWidget({
      widgetKey: 'wk',
      onSubmit: (surveyId, answers, completed) => submissions.push({ surveyId, answers, completed }),
    })
    widget.mount(
      surveyOf([
        question({ id: 'q1', type: 'nps', question: 'How likely are you to recommend us?' }),
        question({ id: 'q2', type: 'text', question: 'Why?' }),
      ]),
    )
    expect(cardText()).toContain('How likely')
    const buttons = card()!.querySelectorAll('.stept-sv-nps button')
    expect(buttons).toHaveLength(11)
    ;(buttons[9] as HTMLButtonElement).click() // picking a score auto-advances
    expect(cardText()).toContain('Why?')

    const submit = card()!.querySelector('.stept-sv-btn:not(.ghost)') as HTMLButtonElement
    expect(submit.textContent).toBe('Submit')
    expect(submit.disabled).toBe(true)
    const area = card()!.querySelector('textarea') as HTMLTextAreaElement
    area.value = 'Fast support'
    area.dispatchEvent(new Event('input'))
    ;(card()!.querySelector('.stept-sv-btn:not(.ghost)') as HTMLButtonElement).click()

    expect(submissions).toEqual([
      {
        surveyId: 'sv-1',
        completed: true,
        answers: [
          { question_id: 'q1', value: 9 },
          { question_id: 'q2', value: 'Fast support' },
        ],
      },
    ])
    expect(card()!.querySelector('.stept-sv-thanks')!.innerHTML).toContain('<strong>feedback</strong>')
  })

  it('keeps the survey name as the dialog label while questions change', () => {
    // Regression: pointing `aria-labelledby` at the question heading overrode
    // the survey's `aria-label`, so the dialog was nameless to assistive tech
    // and appeared to rename itself on every step.
    widget = new SurveyWidget({ widgetKey: 'wk', onSubmit: () => {} })
    widget.mount(
      surveyOf([
        question({ id: 'q1', type: 'nps', question: 'How likely are you to recommend us?' }),
        question({ id: 'q2', type: 'text', question: 'Why?' }),
      ]),
    )
    expect(card()!.getAttribute('aria-label')).toBe('How are we doing?')
    expect(card()!.hasAttribute('aria-labelledby')).toBe(false)
    expect(card()!.getAttribute('aria-describedby')).toBe('stept-sv-q-q1')
    ;(card()!.querySelectorAll('.stept-sv-nps button')[9] as HTMLButtonElement).click()
    expect(card()!.getAttribute('aria-label')).toBe('How are we doing?')
    expect(card()!.getAttribute('aria-describedby')).toBe('stept-sv-q-q2')
  })

  it('records a 1..5 star rating', () => {
    const submissions: Submission[] = []
    widget = new SurveyWidget({
      widgetKey: 'wk',
      onSubmit: (surveyId, answers, completed) => submissions.push({ surveyId, answers, completed }),
    })
    widget.mount(surveyOf([question({ id: 'q1', type: 'rating', question: 'Rate us' })]))
    const stars = card()!.querySelectorAll('.stept-sv-stars button')
    expect(stars).toHaveLength(5)
    ;(stars[3] as HTMLButtonElement).click()
    expect(submissions[0]!.answers).toEqual([{ question_id: 'q1', value: 4 }])
    expect(submissions[0]!.completed).toBe(true)
  })

  it('records a select option', () => {
    const submissions: Submission[] = []
    widget = new SurveyWidget({
      widgetKey: 'wk',
      onSubmit: (surveyId, answers, completed) => submissions.push({ surveyId, answers, completed }),
    })
    widget.mount(
      surveyOf([
        question({ id: 'q1', type: 'select', question: 'Which plan?', options: ['Free', 'Pro'] }),
      ]),
    )
    const options = card()!.querySelectorAll('.stept-sv-options button')
    expect([...options].map((o) => o.textContent)).toEqual(['Free', 'Pro'])
    ;(options[1] as HTMLButtonElement).click()
    expect(submissions[0]!.answers).toEqual([{ question_id: 'q1', value: 'Pro' }])
  })

  it('submits a PARTIAL response when dismissed mid-flow', () => {
    const submissions: Submission[] = []
    const closed: Array<[string, boolean]> = []
    widget = new SurveyWidget({
      widgetKey: 'wk',
      onSubmit: (surveyId, answers, completed) => submissions.push({ surveyId, answers, completed }),
      onClose: (surveyId, completed) => closed.push([surveyId, completed]),
    })
    widget.mount(
      surveyOf([
        question({ id: 'q1', type: 'nps', question: 'Score?' }),
        question({ id: 'q2', type: 'text', question: 'Why?' }),
      ]),
    )
    ;(card()!.querySelectorAll('.stept-sv-nps button')[3] as HTMLButtonElement).click()
    ;(card()!.querySelector('.stept-sv-close') as HTMLButtonElement).click()
    expect(submissions).toEqual([
      { surveyId: 'sv-1', completed: false, answers: [{ question_id: 'q1', value: 3 }] },
    ])
    expect(closed).toEqual([['sv-1', false]])
    expect(card()).toBeNull()
  })

  it('does not POST anything when dismissed without a single answer', () => {
    const submissions: Submission[] = []
    widget = new SurveyWidget({
      widgetKey: 'wk',
      onSubmit: (surveyId, answers, completed) => submissions.push({ surveyId, answers, completed }),
    })
    widget.mount(surveyOf([question({ id: 'q1', type: 'nps', question: 'Score?' })]))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(submissions).toEqual([])
    expect(card()).toBeNull()
  })

  it('renders the modal presentation with a veil and aria-modal', () => {
    widget = new SurveyWidget({ widgetKey: 'wk' })
    widget.mount(surveyOf([question({ id: 'q1', type: 'text', question: 'Thoughts?' })], { presentation: 'modal' }))
    expect(document.querySelector('.stept-sv-veil')).toBeTruthy()
    expect(card()!.classList.contains('stept-sv-modal')).toBe(true)
    expect(card()!.getAttribute('aria-modal')).toBe('true')
    expect(document.activeElement).toBe(card())
  })

  it('offers Skip for optional questions and keeps progress dots in sync', () => {
    widget = new SurveyWidget({ widgetKey: 'wk' })
    widget.mount(
      surveyOf([
        question({ id: 'q1', type: 'text', question: 'Optional', required: false }),
        question({ id: 'q2', type: 'text', question: 'Second' }),
      ]),
    )
    expect(card()!.querySelectorAll('.stept-sv-dots i')).toHaveLength(2)
    expect(card()!.querySelectorAll('.stept-sv-dots i.on')).toHaveLength(1)
    ;(card()!.querySelector('.stept-sv-btn.ghost') as HTMLButtonElement).click()
    expect(cardText()).toContain('Second')
    expect(card()!.querySelectorAll('.stept-sv-dots i.on')).toHaveLength(2)
    expect(widget.answers).toEqual([])
  })
})
