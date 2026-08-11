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
import { useHasPerm } from '@/stores/auth'

import type { Macro, MacroCreate } from '../api'
import { MACRO_ACTION_SPECS, MACRO_ACTION_TYPES } from '../constants'
import { useCreateMacro, useUpdateMacro } from '../hooks'
import {
  deserializeMacroAction,
  emptyActionRow,
  serializeMacroAction,
  type ActionRow,
} from '../lib'
import { ActionRows } from './ActionRows'
import { t } from '@/i18n'

export function MacroEditorDialog({
  open,
  onOpenChange,
  macro,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  macro?: Macro | null
}) {
  const canManage = useHasPerm('conversations:manage')
  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState<'personal' | 'global'>('personal')
  const [actions, setActions] = useState<ActionRow[]>([emptyActionRow()])

  const create = useCreateMacro()
  const update = useUpdateMacro()
  const saving = create.isPending || update.isPending

  useEffect(() => {
    if (!open) return
    if (macro) {
      setName(macro.name)
      setVisibility(macro.visibility === 'global' ? 'global' : 'personal')
      const rows = (macro.actions ?? []).map((a) => deserializeMacroAction(a))
      setActions(rows.length > 0 ? rows : [emptyActionRow()])
    } else {
      setName('')
      setVisibility('personal')
      setActions([emptyActionRow()])
    }
  }, [open, macro])

  const canSave = name.trim().length > 0 && actions.length > 0

  async function save() {
    const body: MacroCreate = {
      name: name.trim(),
      visibility,
      actions: actions.map(serializeMacroAction),
    }
    try {
      if (macro) {
        await update.mutateAsync({ id: macro.id, body })
      } else {
        await create.mutateAsync(body)
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
          <DialogTitle>{macro ? 'Edit macro' : 'New macro'}</DialogTitle>
          <DialogDescription>
            {t('automation.a_saved_set_of_actions_you')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 py-2">
          <div className="grid gap-2">
            <Label htmlFor="macro-name">{t('common.name')}</Label>
            <Input
              id="macro-name"
              value={name}
              placeholder={t('automation.e_g_escalate_to_billing')}
              onChange={(event) => setName(event.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="macro-visibility">{t('automation.visibility')}</Label>
            <NativeSelect
              id="macro-visibility"
              className="w-full"
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as 'personal' | 'global')}
            >
              <NativeSelectOption value="personal">{t('automation.personal_only_you')}</NativeSelectOption>
              <NativeSelectOption value="global" disabled={!canManage}>
                Global — whole workspace{canManage ? '' : ' (requires manage permission)'}
              </NativeSelectOption>
            </NativeSelect>
          </div>

          <Separator />
          <div className="grid gap-2">
            <Label>{t('automation.actions')}</Label>
            <ActionRows
              rows={actions}
              onChange={setActions}
              specs={MACRO_ACTION_SPECS}
              types={MACRO_ACTION_TYPES}
            />
            {actions.length === 0 ? (
              <p className="text-xs text-destructive">{t('automation.add_at_least_one_action')}</p>
            ) : null}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button onClick={save} disabled={!canSave || saving}>
            {saving ? 'Saving…' : macro ? 'Save changes' : 'Create macro'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
