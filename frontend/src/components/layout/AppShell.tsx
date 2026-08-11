import { Outlet } from 'react-router'

import { StepMark } from '@/components/StepMark'
import { AppSidebar } from '@/components/layout/AppSidebar'
import { CommandK } from '@/components/layout/CommandK'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { t } from '@/i18n'

export function AppShell() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="flex h-svh flex-col overflow-hidden">
        {/*
          Below 768px the sidebar collapses into a sheet that only opens from a
          SidebarTrigger. Only the Knowledge and AI shells rendered one, so on a
          phone every other route (inbox, contacts, reports, settings…) lost the
          navigation entirely with no way to get it back. This bar gives every
          route a trigger; the per-feature ones stay for desktop collapsing.
        */}
        <div className="flex items-center gap-2 border-b px-3 py-2 md:hidden">
          <SidebarTrigger />
          <StepMark className="size-4 text-brand" />
          <span className="text-sm font-semibold tracking-tight">{t('common.stept')}</span>
        </div>
        <div className="min-h-0 flex-1">
          <Outlet />
        </div>
      </SidebarInset>
      <CommandK />
    </SidebarProvider>
  )
}
