import type { ReactElement } from 'react'
import { Link, Navigate, useParams } from 'react-router'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useHasPerm } from '@/stores/auth'

import { ApiKeysPanel } from '../components/ApiKeysPanel'
import { AuditPanel } from '../components/AuditPanel'
import { ChannelsPanel } from '../components/ChannelsPanel'
import { MembersPanel } from '../components/MembersPanel'
import { ProfilePanel } from '../components/ProfilePanel'
import { RolesPanel } from '../components/RolesPanel'
import { WorkspacePanel } from '../components/WorkspacePanel'
import { SECTIONS } from '../sections'

const PANELS: Record<string, () => ReactElement> = {
  workspace: () => <WorkspacePanel />,
  members: () => <MembersPanel />,
  roles: () => <RolesPanel />,
  channels: () => <ChannelsPanel />,
  'api-keys': () => <ApiKeysPanel />,
  audit: () => <AuditPanel />,
  profile: () => <ProfilePanel />,
}

export function Component() {
  const { section } = useParams<{ section: string }>()

  // Fixed, unconditional permission lookups (stable hook order).
  const permMap: Record<string, boolean> = {
    'roles:manage': useHasPerm('roles:manage'),
    'conversations:read': useHasPerm('conversations:read'),
    'apikeys:manage': useHasPerm('apikeys:manage'),
    'audit:read': useHasPerm('audit:read'),
  }
  const visible = SECTIONS.filter((s) => !s.perm || permMap[s.perm])

  if (!section) {
    return <Navigate to={`/settings/${visible[0]?.key ?? 'profile'}`} replace />
  }

  const allowed = visible.some((s) => s.key === section)
  const Panel = PANELS[section]

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b px-6 py-4">
        <h1 className="text-lg font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">Manage your workspace, team and account.</p>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-[200px_1fr]">
          <nav className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible" aria-label="Settings sections">
            {visible.map((item) => {
              const Icon = item.icon
              const active = item.key === section
              return (
                <Link
                  key={item.key}
                  to={`/settings/${item.key}`}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
                    active
                      ? 'bg-accent font-medium text-accent-foreground'
                      : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" />
                  {item.label}
                </Link>
              )
            })}
          </nav>

          <div className="min-w-0">
            {!allowed || !Panel ? (
              <Card className="p-6 text-center text-sm text-muted-foreground">
                {allowed ? 'Unknown settings section.' : 'You don’t have access to this section.'}
              </Card>
            ) : (
              <Panel />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default Component
