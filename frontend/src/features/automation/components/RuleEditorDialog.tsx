import { useEffect, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Separator } from '@/components/ui/separator'

import type { AutomationRule, AutomationRuleCreate } from '../api'
import { AUTOMATION_EVENTS } from '../constants'
import { useCreateRule, useUpdateRule } from '../hooks'
import {
  deserializeAction,
  deserializeCondition,
  emptyActionRow,
  serializeAction,
  serializeCondition,
  type ActionRow,
  type ConditionRow,
} from '../lib'
import { ActionRows } from './ActionRows'
import { ConditionRows } from './ConditionRows'

export function RuleEditorDialog({
  open,
  onOpenChange,
  rule,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  rule?: AutomationRule | null
}) {
  const [name, setName] = useState('')
  const [event, setEvent] = useState<string>(AUTOMATION_EVENTS[0]!.value)
  const [conditions, setConditions] = useState<ConditionRow[]>([])
  const [actions, setActions] = useState<ActionRow[]>([emptyActionRow()])

  const create = useCreateRule()
  const update = useUpdateRule()
  const saving = create.isPending || update.isPending

  useEffect(() => {
    if (!open) return
    if (rule) {
      setName(rule.name)
      setEvent(rule.event)
      setConditions((rule.conditions ?? []).map((c) => deserializeCondition(c as never)))
      const rows = (rule.actions ?? []).map((a) => deserializeAction(a as never))
      setActions(rows.length > 0 ? rows : [emptyActionRow()])
    } else {
      setName('')
      setEvent(AUTOMATION_EVENTS[0]!.value)
      setConditions([])
      setActions([emptyActionRow()])
    }
  }, [open, rule])

  const canSave = name.trim().length > 0 && actions.length > 0

  async function save() {
    const body = {
      name: name.trim(),
      event: event as AutomationRuleCreate['event'],
      conditions: conditions.map(serializeCondition),
      actions: actions.map(serializeAction),
    }
    try {
      if (rule) {
        await update.mutateAsync({ id: rule.id, body })
      } else {
        await create.mutateAsync({ ...body, enabled: true, ord: 0 })
      }
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{rule ? 'Edit rule' : 'New rule'}</DialogTitle>
          <DialogDescription>
            When the event fires and all conditions match, the actions run in order.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2">
          <div className="grid gap-2">
            <Label htmlFor="rule-name">Name</Label>
            <Input
              id="rule-name"
              value={name}
              placeholder="e.g. Route billing to finance"
              onChange={(event) => setName(event.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="rule-event">When…</Label>
            <NativeSelect
              id="rule-event"
              className="w-full"
              value={event}
              onChange={(e) => setEvent(e.target.value)}
            >
              {AUTOMATION_EVENTS.map((option) => (
                <NativeSelectOption key={option.value} value={option.value}>
                  {option.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>

          <Separator />
          <div className="grid gap-2">
            <Label>Conditions</Label>
            <ConditionRows rows={conditions} onChange={setConditions} />
          </div>

          <Separator />
          <div className="grid gap-2">
            <Label>Actions</Label>
            <ActionRows rows={actions} onChange={setActions} />
            {actions.length === 0 ? (
              <p className="text-xs text-destructive">Add at least one action.</p>
            ) : null}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || saving}>
            {saving ? 'Saving…' : rule ? 'Save changes' : 'Create rule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
