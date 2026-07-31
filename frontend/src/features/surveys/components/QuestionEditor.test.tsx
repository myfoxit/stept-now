import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { emptyOption, emptyQuestion, type SurveyQuestionDraft } from '../lib'
import { QuestionEditor } from './QuestionEditor'

function Harness({
  initial,
  disabled = false,
}: {
  initial: SurveyQuestionDraft[]
  disabled?: boolean
}) {
  const [questions, setQuestions] = useState(initial)
  return <QuestionEditor questions={questions} disabled={disabled} onChange={setQuestions} />
}

function question(overrides: Partial<SurveyQuestionDraft> = {}): SurveyQuestionDraft {
  return { ...emptyQuestion(), question: 'How are we doing?', ...overrides }
}

describe('QuestionEditor', () => {
  it('renders per-type fields: scale previews, text hint and the options editor', async () => {
    render(<Harness initial={[question({ type: 'text' })]} />)

    expect(screen.getByText(/Answered in a free-text box/)).toBeInTheDocument()
    expect(screen.queryAllByTestId('question-option')).toHaveLength(0)

    await userEvent.selectOptions(screen.getByLabelText('Type'), 'nps')
    // 0..10 scale preview.
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.queryByText(/Answered in a free-text box/)).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Type'), 'rating')
    expect(screen.queryByText('10')).not.toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Type'), 'select')
    expect(screen.getAllByTestId('question-option')).toHaveLength(2)
    expect(screen.getByText('2 of 6 options')).toBeInTheDocument()
  })

  it('adds and removes select options between the 2 and 6 bounds', async () => {
    render(<Harness initial={[question({ type: 'select' })]} />)

    // Two seeded options: neither can be removed (minimum is 2).
    expect(screen.getByRole('button', { name: 'Remove question 1 option 1' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /Add option/ }))
    expect(screen.getAllByTestId('question-option')).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Remove question 1 option 1' })).toBeEnabled()

    await userEvent.type(screen.getByLabelText('Question 1 option 3'), 'Both')
    expect(screen.getByLabelText('Question 1 option 3')).toHaveValue('Both')

    await userEvent.click(screen.getByRole('button', { name: 'Remove question 1 option 3' }))
    expect(screen.getAllByTestId('question-option')).toHaveLength(2)
  })

  it('caps the options editor at 6', async () => {
    render(
      <Harness
        initial={[
          question({
            type: 'select',
            options: ['a', 'b', 'c', 'd', 'e', 'f'].map((v) => emptyOption(v)),
          }),
        ]}
      />
    )
    expect(screen.getByRole('button', { name: /Add option/ })).toBeDisabled()
    expect(screen.getByText('6 of 6 options')).toBeInTheDocument()
  })

  it('reorders questions and caps the list at 10', async () => {
    render(<Harness initial={[question({ question: 'First' }), question({ question: 'Second' })]} />)

    const texts = () =>
      screen.getAllByLabelText('Question').map((input) => (input as HTMLInputElement).value)
    expect(texts()).toEqual(['First', 'Second'])

    expect(screen.getByRole('button', { name: 'Move question 1 up' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Move question 1 down' }))
    expect(texts()).toEqual(['Second', 'First'])

    expect(screen.getByText('2 of 10 questions')).toBeInTheDocument()
  })

  it('caps adding at 10 questions', () => {
    render(<Harness initial={Array.from({ length: 10 }, () => question())} />)
    expect(screen.getByRole('button', { name: /Add question/ })).toBeDisabled()
    expect(screen.getByText('10 of 10 questions')).toBeInTheDocument()
  })

  it('toggles required and disables everything for read-only viewers', async () => {
    const { rerender } = render(<Harness initial={[question()]} />)
    const required = screen.getByRole('switch', { name: 'Question 1 required' })
    expect(required).toBeChecked()
    await userEvent.click(required)
    expect(screen.getByRole('switch', { name: 'Question 1 required' })).not.toBeChecked()

    rerender(<Harness initial={[question()]} disabled />)
    expect(screen.getByLabelText('Type')).toBeDisabled()
    expect(screen.getByRole('button', { name: /Add question/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove question 1' })).toBeDisabled()
  })
})
