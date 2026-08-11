import { LogOut, Moon, Sun } from 'lucide-react'
import { useNavigate } from 'react-router'

import { announceLogout } from '@/api/client'
import { authApi } from '@/features/auth/api'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SidebarMenuButton } from '@/components/ui/sidebar'
import { initials } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'
import { t } from '@/i18n'

function toggleTheme() {
  const dark = document.documentElement.classList.toggle('dark')
  localStorage.setItem('stept-theme', dark ? 'dark' : 'light')
}

export function UserMenu() {
  const navigate = useNavigate()
  const { user, clear } = useAuthStore()

  async function logout() {
    await authApi.logout().catch(() => undefined)
    announceLogout() // other tabs clear + redirect themselves
    clear()
    navigate('/login')
  }

  if (!user) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg">
          <Avatar className="size-8">
            <AvatarFallback>{initials(user.name)}</AvatarFallback>
          </Avatar>
          <div className="grid flex-1 text-left leading-tight">
            <span className="truncate font-medium">{user.name}</span>
            <span className="truncate text-xs text-muted-foreground">{user.email}</span>
          </div>
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56" align="start" side="top">
        <DropdownMenuLabel>{t('common.account')}</DropdownMenuLabel>
        <DropdownMenuItem onClick={toggleTheme}>
          <Sun className="size-4 dark:hidden" />
          <Moon className="hidden size-4 dark:block" />
          {t('common.toggle_theme')}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={logout}>
          <LogOut className="size-4" /> {t('common.log_out')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
