import { Check, ChevronsUpDown, Plus } from 'lucide-react'
import { useNavigate } from 'react-router'

import { StepMark } from '@/components/StepMark'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SidebarMenuButton } from '@/components/ui/sidebar'
import { reconnectRealtime } from '@/api/ws'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

export function WorkspaceSwitcher() {
  const navigate = useNavigate()
  const { memberships, workspaceId, setWorkspace } = useAuthStore()
  const current = memberships.find((m) => m.workspace.id === workspaceId)?.workspace

  function switchTo(id: string) {
    if (id === workspaceId) return
    setWorkspace(id)
    reconnectRealtime()
    navigate('/inbox')
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg" className="flex-1 data-[state=open]:bg-sidebar-accent">
          {/* The product mark, not a workspace initial — the sidebar is Stept's
              own chrome, and the workspace name sits right beside it. */}
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand text-brand-foreground">
            <StepMark className="size-5" title={t('common.stept')} />
          </div>
          <div className="grid flex-1 text-left leading-tight">
            <span className="truncate font-semibold">{current?.name ?? 'Stept'}</span>
            <span className="truncate text-xs text-muted-foreground">{t('common.workspace')}</span>
          </div>
          <ChevronsUpDown className="ml-auto size-4 shrink-0 opacity-50" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-60" align="start">
        {memberships.map((membership) => (
          <DropdownMenuItem
            key={membership.workspace.id}
            onClick={() => switchTo(membership.workspace.id)}
          >
            <span className="flex-1 truncate">{membership.workspace.name}</span>
            {membership.workspace.id === workspaceId ? <Check className="size-4" /> : null}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => navigate('/onboarding')}>
          <Plus className="size-4" /> {t('common.new_workspace')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
