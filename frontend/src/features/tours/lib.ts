/** Pure helpers for the tour step editor — dependency-free for easy unit testing. */

export const PLACEMENTS = [
  { value: 'auto', label: 'Auto' },
  { value: 'top', label: 'Top' },
  { value: 'bottom', label: 'Bottom' },
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
] as const

export interface StepDraft {
  id: string | null
  selector: string
  title: string
  body: string
  placement: string
}

let counter = 0
export function localStepId(): string {
  counter += 1
  return `step-${counter}-${Math.random().toString(36).slice(2, 7)}`
}

export function emptyStep(): StepDraft {
  return { id: null, selector: '', title: '', body: '', placement: 'auto' }
}

/** Move the item at `from` to index `to`, returning a new array (immutable). */
export function moveStep<T>(steps: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= steps.length || to >= steps.length) {
    return steps
  }
  const next = [...steps]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return next
}

/** Serialize a draft step to the wire shape (TourStepIn). Null id ⇒ new step. */
export function serializeStep(step: StepDraft) {
  return {
    ...(step.id ? { id: step.id } : {}),
    selector: step.selector.trim(),
    title: step.title,
    body: step.body,
    placement: step.placement as 'auto' | 'top' | 'bottom' | 'left' | 'right',
  }
}
