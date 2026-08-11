import type { ReactElement } from 'react'
import { Link, Navigate, useParams } from 'react-router'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { useHasPerm } from '@/stores/auth'

import { ApiKeysPanel } from '../components/ApiKeysPanel'
import { AttributesPanel } from '../components/AttributesPanel'
import { AuditPanel } from '../components/AuditPanel'
import { BillingPanel } from '../components/BillingPanel'
import { ChannelsPanel } from '../components/ChannelsPanel'
import { IntegrationsPanel } from '../components/IntegrationsPanel'
import { McpPanel } from '../components/McpPanel'
import { MembersPanel } from '../components/MembersPanel'
import { ProfilePanel } from '../components/ProfilePanel'
import { RolesPanel } from '../components/RolesPanel'
import { SlaPanel } from '../components/SlaPanel'
import { WorkspacePanel } from '../components/WorkspacePanel'
import { WorkingHoursPanel } from '../components/WorkingHoursPanel'
import { SECTIONS } from '../sections'
import { t } from '@/i18n'

const PANELS: Record<string, () => ReactElement> = {
  workspace: () => <WorkspacePanel />,
  members: () => <MembersPanel />,
  roles: () => <RolesPanel />,
  channels: () => <ChannelsPanel />,
  integrations: () => <IntegrationsPanel />,
  billing: () => <BillingPanel />,
  sla: () => <SlaPanel />,
  'working-hours': () => <WorkingHoursPanel />,
  attributes: () => <AttributesPanel />,
  'api-keys': () => <ApiKeysPanel />,
  mcp: () => <McpPanel />,
  audit: () => <AuditPanel />,
  profile: () => <ProfilePanel />,
}

export function Component() {
  const { section } = useParams<{ section: string }>()

  // Fixed, unconditional permission lookups (stable hook order).
  const permMap: Record<string, boolean> = {
    'roles:manage': useHasPerm('roles:manage'),
    'conversations:read': useHasPerm('conversations:read'),
    'integrations:manage': useHasPerm('integrations:manage'),
    'apikeys:manage': useHasPerm('apikeys:manage'),
    'audit:read': useHasPerm('audit:read'),
    'workspace:manage': useHasPerm('workspace:manage'),
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
        <h1 className="text-lg font-semibold">{t('common.settings')}</h1>
        <p className="text-sm text-muted-foreground">{t('settings.manage_your_workspace_team_and_account')}</p>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-[200px_1fr]">
          <nav
            className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible"
            aria-label={t('settings.settings_sections')}
          >
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
