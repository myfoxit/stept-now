/** Small presentational helpers shared across the inbox panes. */

import {
  Bot,
  Clock,
  CheckCircle2,
  Circle,
  Flag,
  Hash,
  Mail,
  MessageSquare,
  Send,
  Webhook,
  type LucideIcon,
} from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { initials } from '@/lib/format'

const CHANNEL_ICONS: Record<string, LucideIcon> = {
  widget: MessageSquare,
  email: Mail,
  slack: Hash,
  telegram: Send,
  api: Webhook,
}

export function ChannelIcon({ channel, className }: { channel: string; className?: string }) {
  const Icon = CHANNEL_ICONS[channel] ?? MessageSquare
  return <Icon className={cn('size-3.5 text-muted-foreground', className)} aria-label={channel} />
}

export function ContactAvatar({
  name,
  avatarUrl,
  online,
  size = 'default',
}: {
  name: string
  avatarUrl?: string | null
  online?: boolean
  size?: 'sm' | 'default' | 'lg'
}) {
  return (
    <span className="relative inline-flex shrink-0">
      <Avatar size={size}>
        {avatarUrl ? <AvatarImage src={avatarUrl} alt={name} /> : null}
        <AvatarFallback>{initials(name || '?') || '?'}</AvatarFallback>
      </Avatar>
      {online ? (
        <span
          className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-background bg-emerald-500"
          aria-label="online"
        />
      ) : null}
    </span>
  )
}

const PRIORITY_STYLES: Record<string, string> = {
  low: 'text-slate-500',
  medium: 'text-blue-500',
  high: 'text-orange-500',
  urgent: 'text-red-500',
}

export function PriorityFlag({ priority, withLabel }: { priority: string; withLabel?: boolean }) {
  if (!priority || priority === 'none') return null
  return (
    <span
      className={cn('inline-flex items-center gap-1 text-xs font-medium', PRIORITY_STYLES[priority])}
      title={`Priority: ${priority}`}
    >
      <Flag className="size-3 fill-current" />
      {withLabel ? <span className="capitalize">{priority}</span> : null}
    </span>
  )
}

const STATUS_META: Record<
  string,
  { label: string; icon: LucideIcon; variant: 'default' | 'secondary' | 'outline' | 'destructive'; className: string }
> = {
  open: { label: 'Open', icon: Circle, variant: 'outline', className: 'text-blue-600 dark:text-blue-400' },
  pending: { label: 'AI', icon: Bot, variant: 'secondary', className: 'text-violet-600 dark:text-violet-400' },
  snoozed: { label: 'Snoozed', icon: Clock, variant: 'outline', className: 'text-amber-600 dark:text-amber-400' },
  resolved: { label: 'Resolved', icon: CheckCircle2, variant: 'outline', className: 'text-emerald-600 dark:text-emerald-400' },
}

export function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? STATUS_META.open
  const Icon = meta.icon
  return (
    <Badge variant={meta.variant} className={cn('gap-1', meta.className)}>
      <Icon className="size-3" />
      {meta.label}
    </Badge>
  )
}
