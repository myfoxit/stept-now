/** "Run macro" dropdown in the thread header. Macros load lazily on first open. */

import { ChevronDown, Wand2 } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useHasPerm } from '@/stores/auth'

import { useRunMacro, useRunnableMacros } from '@/features/inbox/hooks'
import { t } from '@/i18n'

export function RunMacroMenu({ conversationId }: { conversationId: string }) {
  const canManage = useHasPerm('conversations:manage')
  const [open, setOpen] = useState(false)
  const macros = useRunnableMacros(canManage && open)
  const run = useRunMacro(conversationId)

  if (!canManage) return null

  return (
    <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" disabled={run.isPending} aria-label={t('inbox.run_macro')}>
          <Wand2 className="size-4" />
          {t('inbox.macro')}
          <ChevronDown className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>{t('inbox.run_macro')}</DropdownMenuLabel>
        {macros.isLoading ? (
          <DropdownMenuItem disabled>{t('inbox.loading')}</DropdownMenuItem>
        ) : !macros.data || macros.data.length === 0 ? (
          <DropdownMenuItem disabled>{t('common.no_macros_yet')}</DropdownMenuItem>
        ) : (
          macros.data.map((macro) => (
            <DropdownMenuItem key={macro.id} onClick={() => run.mutate(macro.id)}>
              <span className="truncate">{macro.name}</span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
