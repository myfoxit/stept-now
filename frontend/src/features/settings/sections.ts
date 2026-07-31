import {
  Building2,
  Inbox,
  KeyRound,
  ScrollText,
  ShieldCheck,
  UserCog,
  Users,
  type LucideIcon,
} from 'lucide-react'

export interface SectionDef {
  key: string
  label: string
  icon: LucideIcon
  /** Permission required to see the section at all (undefined ⇒ always visible). */
  perm?: string
}

export const SECTIONS: SectionDef[] = [
  { key: 'workspace', label: 'Workspace', icon: Building2 },
  { key: 'members', label: 'Members', icon: Users },
  { key: 'roles', label: 'Roles', icon: ShieldCheck, perm: 'roles:manage' },
  { key: 'channels', label: 'Channels', icon: Inbox, perm: 'conversations:read' },
  { key: 'api-keys', label: 'API keys', icon: KeyRound, perm: 'apikeys:manage' },
  { key: 'audit', label: 'Audit log', icon: ScrollText, perm: 'audit:read' },
  { key: 'profile', label: 'Profile', icon: UserCog },
]
