import {
  BarChart3,
  BookOpen,
  Bot,
  Inbox,
  Map as MapIcon,
  Settings,
  Users,
  Workflow,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'

const NAV = [
  { title: 'Inbox', url: '/inbox', icon: Inbox },
  { title: 'Contacts', url: '/contacts', icon: Users },
  { title: 'Knowledge', url: '/knowledge', icon: BookOpen },
  { title: 'AI Agents', url: '/ai', icon: Bot },
  { title: 'Automation', url: '/automation', icon: Workflow },
  { title: 'Tours', url: '/tours', icon: MapIcon },
  { title: 'Reports', url: '/reports', icon: BarChart3 },
  { title: 'Settings', url: '/settings', icon: Settings },
]

/**
 * Global ⌘K palette. Wave 3 wires the workspace-wide search results
 * (conversations, contacts, articles, docs) underneath the navigation group.
 */
export function CommandK() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setOpen((value) => !value)
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Search or jump to…" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Go to">
          {NAV.map((item) => (
            <CommandItem
              key={item.url}
              onSelect={() => {
                setOpen(false)
                navigate(item.url)
              }}
            >
              <item.icon className="size-4" />
              {item.title}
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
