import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'

import type { QuestionType } from '../api'
import {
  emptyOption,
  emptyQuestion,
  MAX_OPTIONS,
  MAX_QUESTIONS,
  MIN_OPTIONS,
  moveQuestion,
  QUESTION_TYPES,
  type QuestionOptionDraft,
  type SurveyQuestionDraft,
} from '../lib'
import { t } from '@/i18n'

/** The 0–10 / 1–5 scales the widget renders; shown here as a read-only preview. */
function ScalePreview({ type }: { type: QuestionType }) {
  const values = type === 'nps' ? Array.from({ length: 11 }, (_, i) => i) : [1, 2, 3, 4, 5]
  return (
    <div className="flex flex-wrap gap-1" aria-hidden>
      {values.map((value) => (
        <span
          key={value}
          className="inline-flex size-7 items-center justify-center rounded-md border text-xs text-muted-foreground"
        >
          {value}
        </span>
      ))}
    </div>
  )
}

function OptionsEditor({
  index,
  options,
  disabled,
  onChange,
}: {
  index: number
  options: QuestionOptionDraft[]
  disabled: boolean
  onChange: (options: QuestionOptionDraft[]) => void
}) {
  return (
    <div className="grid gap-2">
      <Label>{t('common.options')}</Label>
      {options.map((option, optionIndex) => (
        <div key={option.key} className="flex items-center gap-2" data-testid="question-option">
          <Input
            aria-label={`Question ${index + 1} option ${optionIndex + 1}`}
            placeholder={`Option ${optionIndex + 1}`}
            value={option.value}
            disabled={disabled}
            onChange={(e) =>
              onChange(
                options.map((o, i) => (i === optionIndex ? { ...o, value: e.target.value } : o))
              )
            }
          />
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label={`Remove question ${index + 1} option ${optionIndex + 1}`}
            disabled={disabled || options.length <= MIN_OPTIONS}
            onClick={() => onChange(options.filter((_, i) => i !== optionIndex))}
          >
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || options.length >= MAX_OPTIONS}
          onClick={() => onChange([...options, emptyOption()])}
        >
          <Plus className="size-4" /> {t('surveys.add_option')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {options.length} of {MAX_OPTIONS} options
        </span>
      </div>
    </div>
  )
}

/** Ordered survey questions: type, prompt, required flag and select options. */
export function QuestionEditor({
  questions,
  onChange,
  disabled = false,
}: {
  questions: SurveyQuestionDraft[]
  onChange: (questions: SurveyQuestionDraft[]) => void
  disabled?: boolean
}) {
  function update(index: number, patch: Partial<SurveyQuestionDraft>) {
    onChange(questions.map((question, i) => (i === index ? { ...question, ...patch } : question)))
  }
  function move(index: number, direction: -1 | 1) {
    onChange(moveQuestion(questions, index, index + direction))
  }

  return (
    <div className="grid gap-3">
      {questions.length === 0 ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t('surveys.no_questions_yet_start_with_an')}
        </p>
      ) : null}

      {questions.map((question, index) => {
        const meta = QUESTION_TYPES.find((type) => type.value === question.type)
        return (
          <Card key={question.key} className="gap-3 p-4" data-testid="survey-question">
            <div className="flex items-center gap-2">
              <GripVertical className="size-4 text-muted-foreground" aria-hidden />
              <span className="text-sm font-medium">Question {index + 1}</span>
              <div className="flex-1" />
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={`Move question ${index + 1} up`}
                disabled={disabled || index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={`Move question ${index + 1} down`}
                disabled={disabled || index === questions.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                aria-label={`Remove question ${index + 1}`}
                disabled={disabled}
                onClick={() => onChange(questions.filter((_, i) => i !== index))}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>

            <div className="grid gap-3 sm:grid-cols-[200px_1fr]">
              <div className="grid gap-1.5">
                <Label htmlFor={`question-type-${index}`}>{t('common.type')}</Label>
                <NativeSelect
                  id={`question-type-${index}`}
                  className="w-full"
                  value={question.type}
                  disabled={disabled}
                  onChange={(e) => update(index, { type: e.target.value as QuestionType })}
                >
                  {QUESTION_TYPES.map((type) => (
                    <NativeSelectOption key={type.value} value={type.value}>
                      {type.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`question-text-${index}`}>{t('surveys.question')}</Label>
                <Input
                  id={`question-text-${index}`}
                  placeholder={meta?.hint}
                  value={question.question}
                  disabled={disabled}
                  onChange={(e) => update(index, { question: e.target.value })}
                />
              </div>
            </div>

            {question.type === 'nps' || question.type === 'rating' ? (
              <ScalePreview type={question.type} />
            ) : null}

            {question.type === 'text' ? (
              <p className="text-xs text-muted-foreground">
                {t('surveys.answered_in_a_free_text_box')}
              </p>
            ) : null}

            {question.type === 'select' ? (
              <OptionsEditor
                index={index}
                options={question.options}
                disabled={disabled}
                onChange={(options) => update(index, { options })}
              />
            ) : null}

            <div className="flex items-center justify-between gap-3">
              <Label htmlFor={`question-required-${index}`}>{t('surveys.required')}</Label>
              <Switch
                id={`question-required-${index}`}
                aria-label={`Question ${index + 1} required`}
                checked={question.required}
                disabled={disabled}
                onCheckedChange={(checked) => update(index, { required: checked })}
              />
            </div>
          </Card>
        )
      })}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || questions.length >= MAX_QUESTIONS}
          onClick={() => onChange([...questions, emptyQuestion()])}
        >
          <Plus className="size-4" /> {t('surveys.add_question')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {questions.length} of {MAX_QUESTIONS} questions
        </span>
      </div>
    </div>
  )
}
