import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Textarea } from '@/components/ui/textarea'

import type { ChecklistActionType, ChecklistCompletionType, TourOption } from '../api'
import {
  ACTION_TYPES,
  COMPLETION_TYPES,
  emptyItem,
  MAX_ITEMS,
  moveItem,
  type ChecklistItemDraft,
} from '../lib'
import { t } from '@/i18n'

function TourPicker({
  id,
  label,
  value,
  tours,
  disabled,
  onChange,
}: {
  id: string
  label: string
  value: string
  tours: TourOption[]
  disabled: boolean
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect
        id={id}
        className="w-full"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <NativeSelectOption value="">{t('checklists.select_a_tour')}</NativeSelectOption>
        {tours.map((tour) => (
          <NativeSelectOption key={tour.id} value={tour.id}>
            {tour.name}
            {tour.status === 'live' ? '' : ` (${tour.status})`}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  )
}

/** Ordered checklist items: title, markdown body, CTA action and completion rule. */
export function ItemEditor({
  items,
  tours,
  onChange,
  disabled = false,
}: {
  items: ChecklistItemDraft[]
  tours: TourOption[]
  onChange: (items: ChecklistItemDraft[]) => void
  disabled?: boolean
}) {
  function update(index: number, patch: Partial<ChecklistItemDraft>) {
    onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }
  function move(index: number, direction: -1 | 1) {
    onChange(moveItem(items, index, index + direction))
  }

  const atLimit = items.length >= MAX_ITEMS

  return (
    <div className="grid gap-3">
      {items.length === 0 ? (
        <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t('checklists.no_items_yet_add_the_first')}
        </p>
      ) : null}

      {items.map((item, index) => (
        <Card key={item.key} className="gap-3 p-4" data-testid="checklist-item">
          <div className="flex items-center gap-2">
            <GripVertical className="size-4 text-muted-foreground" aria-hidden />
            <span className="text-sm font-medium">Item {index + 1}</span>
            <div className="flex-1" />
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Move item ${index + 1} up`}
              disabled={disabled || index === 0}
              onClick={() => move(index, -1)}
            >
              <ArrowUp className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Move item ${index + 1} down`}
              disabled={disabled || index === items.length - 1}
              onClick={() => move(index, 1)}
            >
              <ArrowDown className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label={`Remove item ${index + 1}`}
              disabled={disabled}
              onClick={() => onChange(items.filter((_, i) => i !== index))}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor={`item-title-${index}`}>{t('common.title')}</Label>
            <Input
              id={`item-title-${index}`}
              placeholder={t('checklists.take_the_product_tour')}
              value={item.title}
              disabled={disabled}
              onChange={(e) => update(index, { title: e.target.value })}
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor={`item-body-${index}`}>Description (markdown)</Label>
            <Textarea
              id={`item-body-${index}`}
              rows={2}
              placeholder={t('checklists.a_two_minute_walkthrough_of_the')}
              value={item.body}
              disabled={disabled}
              onChange={(e) => update(index, { body: e.target.value })}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor={`item-action-${index}`}>{t('checklists.button_action')}</Label>
                <NativeSelect
                  id={`item-action-${index}`}
                  className="w-full"
                  value={item.actionType}
                  disabled={disabled}
                  onChange={(e) =>
                    update(index, { actionType: e.target.value as ChecklistActionType })
                  }
                >
                  {ACTION_TYPES.map((action) => (
                    <NativeSelectOption key={action.value} value={action.value}>
                      {action.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {item.actionType === 'start_tour' ? (
                <TourPicker
                  id={`item-action-tour-${index}`}
                  label={t('checklists.tour_to_start')}
                  value={item.actionTourId}
                  tours={tours}
                  disabled={disabled}
                  onChange={(value) => update(index, { actionTourId: value })}
                />
              ) : null}
              {item.actionType === 'open_url' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`item-action-url-${index}`}>{t('checklists.url_to_open')}</Label>
                  <Input
                    id={`item-action-url-${index}`}
                    className="font-mono text-xs"
                    placeholder="https://docs.example.com/setup"
                    value={item.actionUrl}
                    disabled={disabled}
                    onChange={(e) => update(index, { actionUrl: e.target.value })}
                  />
                </div>
              ) : null}
            </div>

            <div className="grid gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor={`item-completion-${index}`}>{t('checklists.completes_when')}</Label>
                <NativeSelect
                  id={`item-completion-${index}`}
                  className="w-full"
                  value={item.completionType}
                  disabled={disabled}
                  onChange={(e) =>
                    update(index, { completionType: e.target.value as ChecklistCompletionType })
                  }
                >
                  {COMPLETION_TYPES.map((completion) => (
                    <NativeSelectOption key={completion.value} value={completion.value}>
                      {completion.label}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </div>
              {item.completionType === 'tour_completed' ? (
                <TourPicker
                  id={`item-completion-tour-${index}`}
                  label={t('checklists.tour_that_completes_it')}
                  value={item.completionTourId}
                  tours={tours}
                  disabled={disabled}
                  onChange={(value) => update(index, { completionTourId: value })}
                />
              ) : null}
              {item.completionType === 'url_visited' ? (
                <div className="grid gap-1.5">
                  <Label htmlFor={`item-completion-url-${index}`}>{t('common.url_pattern')}</Label>
                  <Input
                    id={`item-completion-url-${index}`}
                    className="font-mono text-xs"
                    placeholder="*/settings*"
                    value={item.completionUrlPattern}
                    disabled={disabled}
                    onChange={(e) => update(index, { completionUrlPattern: e.target.value })}
                  />
                </div>
              ) : null}
            </div>
          </div>
        </Card>
      ))}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || atLimit}
          onClick={() => onChange([...items, emptyItem()])}
        >
          <Plus className="size-4" /> {t('checklists.add_item')}
        </Button>
        <span className="text-xs text-muted-foreground">
          {items.length} of {MAX_ITEMS} items
        </span>
      </div>
    </div>
  )
}
