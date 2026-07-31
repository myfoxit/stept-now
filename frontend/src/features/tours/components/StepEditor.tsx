import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'

import { emptyStep, localStepId, moveStep, PLACEMENTS, type StepDraft } from '../lib'

export function StepEditor({
  steps,
  onChange,
  disabled = false,
}: {
  steps: StepDraft[]
  onChange: (steps: StepDraft[]) => void
  disabled?: boolean
}) {
  function update(index: number, patch: Partial<StepDraft>) {
    onChange(steps.map((step, i) => (i === index ? { ...step, ...patch } : step)))
  }
  function move(index: number, direction: -1 | 1) {
    onChange(moveStep(steps, index, index + direction))
  }
  function remove(index: number) {
    onChange(steps.filter((_, i) => i !== index))
  }
  function add() {
    onChange([...steps, { ...emptyStep(), id: localStepId() }])
  }

  return (
    <div className="grid gap-3">
      {steps.length === 0 ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          No steps yet. Add one, or connect the recorder to capture them from your app.
        </p>
      ) : null}

      {steps.map((step, index) => (
        <Card key={step.id ?? index} className="gap-3 p-4" data-testid="tour-step">
          <div className="flex items-center gap-2">
            <GripVertical className="size-4 text-muted-foreground" aria-hidden />
            <span className="text-sm font-medium">Step {index + 1}</span>
            <div className="flex-1" />
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Move step ${index + 1} up`}
              disabled={disabled || index === 0}
              onClick={() => move(index, -1)}
            >
              <ArrowUp className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Move step ${index + 1} down`}
              disabled={disabled || index === steps.length - 1}
              onClick={() => move(index, 1)}
            >
              <ArrowDown className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Remove step ${index + 1}`}
              disabled={disabled}
              onClick={() => remove(index)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor={`selector-${index}`}>CSS selector</Label>
              <Input
                id={`selector-${index}`}
                className="font-mono text-xs"
                placeholder="#signup-btn"
                value={step.selector}
                disabled={disabled}
                onChange={(e) => update(index, { selector: e.target.value })}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`placement-${index}`}>Placement</Label>
              <NativeSelect
                id={`placement-${index}`}
                className="w-full"
                value={step.placement}
                disabled={disabled}
                onChange={(e) => update(index, { placement: e.target.value })}
              >
                {PLACEMENTS.map((placement) => (
                  <NativeSelectOption key={placement.value} value={placement.value}>
                    {placement.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor={`title-${index}`}>Title</Label>
            <Input
              id={`title-${index}`}
              placeholder="Welcome!"
              value={step.title}
              disabled={disabled}
              onChange={(e) => update(index, { title: e.target.value })}
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor={`body-${index}`}>Body (markdown)</Label>
            <Textarea
              id={`body-${index}`}
              rows={3}
              placeholder="Explain what this feature does…"
              value={step.body}
              disabled={disabled}
              onChange={(e) => update(index, { body: e.target.value })}
            />
          </div>
        </Card>
      ))}

      <div>
        <Button type="button" variant="outline" size="sm" onClick={add} disabled={disabled}>
          <Plus className="size-4" /> Add step
        </Button>
      </div>
    </div>
  )
}
