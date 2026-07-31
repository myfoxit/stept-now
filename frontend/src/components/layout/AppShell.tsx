import { Outlet } from 'react-router'

import { AppSidebar } from '@/components/layout/AppSidebar'
import { CommandK } from '@/components/layout/CommandK'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'

export function AppShell() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="h-svh overflow-hidden">
        <Outlet />
      </SidebarInset>
      <CommandK />
    </SidebarProvider>
  )
}
