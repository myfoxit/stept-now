import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Crosshair,
  ImageUp,
  MessageSquare,
  MousePointerClick,
  Plus,
  RectangleHorizontal,
  Settings2,
  Square,
  Trash2,
} from 'lucide-react'
import { useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth'

import type { StepType } from '../api'
import { useUploadStepMedia } from '../hooks'
import {
  ACTION_KINDS,
  ADVANCE_MODES,
  emptyStep,
  hasContent,
  moveStep,
  needsSelector,
  PLACEMENTS,
  STEP_TYPES,
  WAIT_FOR,
  type ActionKind,
  type AdvanceOn,
  type StepDraft,
  type WaitFor,
} from '../lib'
import { SelectorChips } from './SelectorChips'
import { StepShot } from './StepShot'

/** Image/video URL with an upload button writing to the public media namespace. */
function MediaField({
  index,
  draft,
  onChange,
  disabled,
}: {
  index: number
  draft: StepDraft
  onChange: (patch: Partial<StepDraft>) => void
  disabled: boolean
}) {
  const upload = useUploadStepMedia()
  const inputRef = useRef<HTMLInputElement>(null)

  async function pick(file: File | undefined) {
    if (!file) return
    const uploaded = await upload.mutateAsync(file)
    onChange({
      mediaUrl: uploaded.url,
      mediaType: uploaded.content_type.startsWith('video/') ? 'video' : 'image',
    })
  }

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={`media-url-${index}`}>Media</Label>
      <div className="flex items-center gap-2">
        <NativeSelect
          className="w-28 shrink-0"
          aria-label="Media type"
          value={draft.mediaType}
          disabled={disabled}
          onChange={(e) => onChange({ mediaType: e.target.value as 'image' | 'video' })}
        >
          <NativeSelectOption value="image">Image</NativeSelectOption>
          <NativeSelectOption value="video">Video</NativeSelectOption>
        </NativeSelect>
        <Input
          id={`media-url-${index}`}
          className="text-xs"
          placeholder="https://… or upload"
          value={draft.mediaUrl}
          disabled={disabled}
          onChange={(e) => onChange({ mediaUrl: e.target.value })}
        />
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml,video/mp4"
          data-testid={`media-file-${index}`}
          onChange={(e) => {
            void pick(e.target.files?.[0])
            e.target.value = ''
          }}
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="shrink-0"
          aria-label={`Upload media for step ${index + 1}`}
          disabled={disabled || upload.isPending}
          onClick={() => inputRef.current?.click()}
        >
          {upload.isPending ? <Spinner className="size-4" /> : <ImageUp className="size-4" />}
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Uploads are stored publicly so they render on your customers’ site.
      </p>
    </div>
  )
}

/** A labelled pair of "button text" + "link" inputs. */
function CtaFields({
  index,
  legend,
  hint,
  label,
  url,
  disabled,
  onLabel,
  onUrl,
}: {
  index: number
  legend: string
  hint: string
  label: string
  url: string
  disabled: boolean
  onLabel: (value: string) => void
  onUrl: (value: string) => void
}) {
  const slug = legend.toLowerCase().replace(/\s+/g, '-')
  return (
    <fieldset className="grid gap-1.5">
      <legend className="text-xs font-medium">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          aria-label={`${legend} text`}
          id={`${slug}-label-${index}`}
          placeholder="Button text"
          value={label}
          disabled={disabled}
          onChange={(e) => onLabel(e.target.value)}
        />
        <Input
          aria-label={`${legend} link`}
          id={`${slug}-url-${index}`}
          className="font-mono text-xs"
          placeholder="https://… (optional)"
          value={url}
          disabled={disabled}
          onChange={(e) => onUrl(e.target.value)}
        />
      </div>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </fieldset>
  )
}

/**
 * One step.
 *
 * The card leads with what the author recognises — the screenshot the recorder
 * took and the words shown on top of it. Targeting, timing and button config
 * are real but secondary: they live behind one disclosure so a recorded flow
 * reads as a storyboard, not a form. `defaultOpen` is off even for
 * hand-authored steps, because "add a step then write the copy" is the common
 * path and the selector can wait.
 */
function StepCard({
  draft,
  index,
  total,
  disabled,
  selected,
  workspaceId,
  onSelect,
  onChange,
  onMove,
  onRemove,
}: {
  draft: StepDraft
  index: number
  total: number
  disabled: boolean
  selected: boolean
  workspaceId: string
  onSelect: () => void
  onChange: (patch: Partial<StepDraft>) => void
  onMove: (direction: -1 | 1) => void
  onRemove: () => void
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const showSelector = needsSelector(draft.type)
  const showContent = hasContent(draft.type)
  const showAdvance = showContent || draft.type === 'action'
  const captured = Boolean(draft.screenshotKey)

  return (
    <Card
      className={cn('gap-3 p-4', selected && 'border-primary/60 ring-1 ring-primary/30')}
      data-testid="tour-step"
      onFocusCapture={onSelect}
      onClick={onSelect}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
          {index + 1}
        </span>
        <NativeSelect
          id={`step-type-${index}`}
          aria-label="Type"
          className="w-36"
          value={draft.type}
          disabled={disabled}
          onChange={(e) => onChange({ type: e.target.value as StepType })}
        >
          {STEP_TYPES.map((type) => (
            <NativeSelectOption key={type.value} value={type.value}>
              {type.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        {draft.target ? (
          <Badge variant="secondary" className="gap-1" data-testid="recorder-target">
            <Crosshair aria-hidden />
            Captured by recorder · {draft.fallbackSelectors.length} fallback
            {draft.fallbackSelectors.length === 1 ? '' : 's'}
          </Badge>
        ) : null}
        {draft.sandboxKey ? (
          <Badge variant="outline" className="gap-1" data-testid="sandbox-ready">
            Sandbox ready
          </Badge>
        ) : null}
        <div className="flex-1" />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Move step ${index + 1} up`}
          disabled={disabled || index === 0}
          onClick={() => onMove(-1)}
        >
          <ArrowUp className="size-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Move step ${index + 1} down`}
          disabled={disabled || index === total - 1}
          onClick={() => onMove(1)}
        >
          <ArrowDown className="size-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Remove step ${index + 1}`}
          disabled={disabled}
          onClick={onRemove}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>

      {/* --- the guide: screenshot + the words shown on it -------------- */}
      {showContent ? (
        <div className={cn('grid gap-3', captured && 'sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]')}>
          {captured ? (
            <figure className="grid gap-1">
              <StepShot draft={draft} workspaceId={workspaceId} index={index} />
              <figcaption className="text-[11px] text-muted-foreground">
                Captured while recording — shown here only, never to end users.
              </figcaption>
            </figure>
          ) : null}
          <div className="grid content-start gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor={`title-${index}`}>Title</Label>
              <Input
                id={`title-${index}`}
                placeholder="Welcome!"
                value={draft.title}
                disabled={disabled}
                onChange={(e) => onChange({ title: e.target.value })}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`body-${index}`}>Description</Label>
              <Textarea
                id={`body-${index}`}
                rows={captured ? 5 : 3}
                placeholder="Explain what this feature does…"
                value={draft.body}
                disabled={disabled}
                onChange={(e) => onChange({ body: e.target.value })}
              />
              <p className="text-[11px] text-muted-foreground">Markdown is supported.</p>
            </div>
          </div>
        </div>
      ) : (
        // action / wait steps have no authored copy: show what they DO instead.
        <div className="grid gap-3">
          {captured ? (
            <StepShot draft={draft} workspaceId={workspaceId} index={index} className="max-w-md" />
          ) : null}
          <p className="text-xs text-muted-foreground">
            {STEP_TYPES.find((t) => t.value === draft.type)?.hint}. Nothing is written for this
            step — configure it under Advanced.
          </p>
        </div>
      )}

      {/* --- everything else ------------------------------------------- */}
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 h-8 justify-start gap-1.5 text-xs text-muted-foreground hover:text-foreground"
            aria-expanded={advancedOpen}
          >
            <ChevronRight className={cn('size-3.5 transition-transform', advancedOpen && 'rotate-90')} />
            <Settings2 className="size-3.5" />
            Advanced
            <span className="font-normal opacity-70">
              — targeting, buttons{showAdvance ? ', timing' : ''}
            </span>
          </Button>
        </CollapsibleTrigger>

        <CollapsibleContent className="grid gap-3 pt-3">
          {showContent ? (
            <div className="grid gap-1.5">
              <Label htmlFor={`placement-${index}`}>Placement</Label>
              <NativeSelect
                id={`placement-${index}`}
                className="w-full"
                value={draft.placement}
                disabled={disabled}
                onChange={(e) => onChange({ placement: e.target.value as StepDraft['placement'] })}
              >
                {PLACEMENTS.map((placement) => (
                  <NativeSelectOption key={placement.value} value={placement.value}>
                    {placement.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
          ) : null}

          {showSelector ? (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor={`selector-${index}`}>CSS selector</Label>
                <Input
                  id={`selector-${index}`}
                  className="font-mono text-xs"
                  placeholder="#signup-btn"
                  value={draft.selector}
                  disabled={disabled}
                  onChange={(e) => onChange({ selector: e.target.value })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`fallbacks-${index}`}>Fallback selectors</Label>
                <SelectorChips
                  inputId={`fallbacks-${index}`}
                  label={`Add fallback selector for step ${index + 1}`}
                  values={draft.fallbackSelectors}
                  disabled={disabled}
                  onChange={(fallbackSelectors) => onChange({ fallbackSelectors })}
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor={`hint-${index}`}>Text hint</Label>
                <Input
                  id={`hint-${index}`}
                  placeholder="Save changes"
                  value={draft.textHint}
                  disabled={disabled}
                  onChange={(e) => onChange({ textHint: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground">
                  Visible text the player scans for when every selector misses.
                </p>
              </div>
            </>
          ) : null}

          {showContent ? (
            <>
              <MediaField index={index} draft={draft} onChange={onChange} disabled={disabled} />
              <CtaFields
                index={index}
                legend="Primary button"
                hint="Leave blank for the player’s own Next / Got it button."
                label={draft.ctaLabel}
                url={draft.ctaUrl}
                disabled={disabled}
                onLabel={(ctaLabel) => onChange({ ctaLabel })}
                onUrl={(ctaUrl) => onChange({ ctaUrl })}
              />
              <CtaFields
                index={index}
                legend="Secondary button"
                hint="An optional second button, e.g. “Read the docs”."
                label={draft.secondaryCtaLabel}
                url={draft.secondaryCtaUrl}
                disabled={disabled}
                onLabel={(secondaryCtaLabel) => onChange({ secondaryCtaLabel })}
                onUrl={(secondaryCtaUrl) => onChange({ secondaryCtaUrl })}
              />
            </>
          ) : null}

          {draft.type === 'action' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`action-kind-${index}`}>Action</Label>
                <NativeSelect
                  id={`action-kind-${index}`}
                  className="w-full"
                  value={draft.actionKind}
                  disabled={disabled}
                  onChange={(e) => onChange({ actionKind: e.target.value as ActionKind })}
                >
                  {ACTION_KINDS.map((kind) => (
                    <NativeSelectOption key={kind.value} value={kind.value}>
                      {kind.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {draft.actionKind === 'fill' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`action-value-${index}`}>Value to type</Label>
                  <Input
                    id={`action-value-${index}`}
                    value={draft.actionValue}
                    disabled={disabled}
                    onChange={(e) => onChange({ actionValue: e.target.value })}
                  />
                </div>
              ) : null}
              {draft.actionKind === 'navigate' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`action-url-${index}`}>Destination URL</Label>
                  <Input
                    id={`action-url-${index}`}
                    className="font-mono text-xs"
                    placeholder="/settings/billing"
                    value={draft.actionUrl}
                    disabled={disabled}
                    onChange={(e) => onChange({ actionUrl: e.target.value })}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          {draft.type === 'wait' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`wait-for-${index}`}>Wait for</Label>
                <NativeSelect
                  id={`wait-for-${index}`}
                  className="w-full"
                  value={draft.waitFor}
                  disabled={disabled}
                  onChange={(e) => onChange({ waitFor: e.target.value as WaitFor })}
                >
                  {WAIT_FOR.map((option) => (
                    <NativeSelectOption key={option.value} value={option.value}>
                      {option.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {draft.waitFor === 'element' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`wait-selector-${index}`}>Selector to wait for</Label>
                  <Input
                    id={`wait-selector-${index}`}
                    className="font-mono text-xs"
                    placeholder="[data-loaded]"
                    value={draft.waitSelector}
                    disabled={disabled}
                    onChange={(e) => onChange({ waitSelector: e.target.value })}
                  />
                </div>
              ) : (
                <div className="grid gap-1.5">
                  <Label htmlFor={`wait-url-${index}`}>URL pattern</Label>
                  <Input
                    id={`wait-url-${index}`}
                    className="font-mono text-xs"
                    placeholder="*/onboarding/done*"
                    value={draft.waitUrlPattern}
                    disabled={disabled}
                    onChange={(e) => onChange({ waitUrlPattern: e.target.value })}
                  />
                </div>
              )}
              <div className="grid gap-1.5">
                <Label htmlFor={`wait-timeout-${index}`}>Timeout (ms)</Label>
                <Input
                  id={`wait-timeout-${index}`}
                  type="number"
                  min={100}
                  step={500}
                  value={draft.waitTimeoutMs}
                  disabled={disabled}
                  onChange={(e) => onChange({ waitTimeoutMs: e.target.value })}
                />
              </div>
            </div>
          ) : null}

          {showAdvance ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor={`advance-${index}`}>Advance</Label>
                <NativeSelect
                  id={`advance-${index}`}
                  className="w-full"
                  value={draft.advanceOn}
                  disabled={disabled}
                  onChange={(e) => onChange({ advanceOn: e.target.value as AdvanceOn })}
                >
                  {ADVANCE_MODES.map((mode) => (
                    <NativeSelectOption key={mode.value} value={mode.value}>
                      {mode.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {draft.advanceOn === 'delay' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`delay-${index}`}>Delay (ms)</Label>
                  <Input
                    id={`delay-${index}`}
                    type="number"
                    min={100}
                    step={500}
                    value={draft.delayMs}
                    disabled={disabled}
                    onChange={(e) => onChange({ delayMs: e.target.value })}
                  />
                </div>
              ) : null}
            </div>
          ) : null}
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}

/** The step kinds worth one click; the rest are a type change away. */
const QUICK_ADD: { type: StepType; label: string; Icon: typeof MessageSquare }[] = [
  { type: 'tooltip', label: 'Tooltip', Icon: MessageSquare },
  { type: 'modal', label: 'Modal', Icon: Square },
  { type: 'banner', label: 'Banner', Icon: RectangleHorizontal },
  { type: 'hotspot', label: 'Hotspot', Icon: MousePointerClick },
]

/**
 * The step list. Reordering is button-based on purpose: no drag-and-drop
 * library is available, and a grip handle that does nothing is worse than
 * honest arrows.
 */
export function StepEditor({
  steps,
  onChange,
  disabled = false,
  selectedKey,
  onSelect,
}: {
  steps: StepDraft[]
  onChange: (steps: StepDraft[]) => void
  disabled?: boolean
  selectedKey?: string | null
  onSelect?: (key: string) => void
}) {
  const workspaceId = useAuthStore((state) => state.workspaceId) ?? ''

  function update(index: number, patch: Partial<StepDraft>) {
    onChange(steps.map((step, i) => (i === index ? { ...step, ...patch } : step)))
  }

  return (
    <div className="grid gap-3">
      {steps.length === 0 ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          No steps yet. Add one below, or record them from your app with the Chrome extension.
        </p>
      ) : null}

      {steps.map((step, index) => (
        <StepCard
          key={step.key}
          draft={step}
          index={index}
          total={steps.length}
          disabled={disabled}
          workspaceId={workspaceId}
          selected={selectedKey === step.key}
          onSelect={() => onSelect?.(step.key)}
          onChange={(patch) => update(index, patch)}
          onMove={(direction) => onChange(moveStep(steps, index, index + direction))}
          onRemove={() => onChange(steps.filter((_, i) => i !== index))}
        />
      ))}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Add</span>
        {QUICK_ADD.map(({ type, label, Icon }) => (
          <Button
            key={type}
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            aria-label={`Add ${label.toLowerCase()} step`}
            onClick={() => onChange([...steps, emptyStep(type)])}
          >
            <Icon className="size-4" /> {label}
          </Button>
        ))}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => onChange([...steps, emptyStep()])}
        >
          <Plus className="size-4" /> Add step
        </Button>
      </div>
    </div>
  )
}
