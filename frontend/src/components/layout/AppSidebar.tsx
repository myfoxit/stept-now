import {
  BarChart3,
  BookOpen,
  Bot,
  Inbox,
  Map as MapIcon,
  Megaphone,
  Settings,
  Users,
  Workflow,
} from 'lucide-react'
import { NavLink, useLocation } from 'react-router'

import { NotificationsBell } from '@/components/layout/NotificationsBell'
import { UserMenu } from '@/components/layout/UserMenu'
import { WorkspaceSwitcher } from '@/components/layout/WorkspaceSwitcher'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'

const SECTIONS = [
  {
    label: 'Support',
    items: [
      { title: 'Inbox', url: '/inbox', icon: Inbox },
      { title: 'Contacts', url: '/contacts', icon: Users },
    ],
  },
  {
    label: 'Automation & AI',
    items: [
      { title: 'Knowledge', url: '/knowledge', icon: BookOpen },
      { title: 'AI Agents', url: '/ai', icon: Bot },
      { title: 'Automation', url: '/automation', icon: Workflow },
      { title: 'Campaigns', url: '/campaigns', icon: Megaphone },
      { title: 'Tours', url: '/tours', icon: MapIcon },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { title: 'Reports', url: '/reports', icon: BarChart3 },
      { title: 'Settings', url: '/settings', icon: Settings },
    ],
  },
]

export function AppSidebar() {
  const location = useLocation()
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-1">
          <WorkspaceSwitcher />
          <NotificationsBell />
        </div>
      </SidebarHeader>
      <SidebarContent>
        {SECTIONS.map((section) => (
          <SidebarGroup key={section.label}>
            <SidebarGroupLabel>{section.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {section.items.map((item) => (
                  <SidebarMenuItem key={item.url}>
                    <SidebarMenuButton
                      asChild
                      isActive={
                        location.pathname === item.url ||
                        location.pathname.startsWith(`${item.url}/`)
                      }
                      tooltip={item.title}
                    >
                      {/* data-tour anchors are stable selectors for DAP product tours */}
                      <NavLink to={item.url} data-tour={item.url.slice(1)}>
                        <item.icon />
                        <span>{item.title}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <UserMenu />
      </SidebarFooter>
    </Sidebar>
  )
}
