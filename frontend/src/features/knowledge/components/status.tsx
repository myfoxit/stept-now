/** Status pills + source-type iconography shared across knowledge screens. */

import {
  AlertCircle,
  CheckCircle2,
  Clock,
  FileText,
  Files,
  Github,
  Globe,
  KeyRound,
  Link2,
  Loader2,
  Map,
  NotebookText,
  RefreshCw,
  Type,
  XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { t } from '@/i18n'

const SOURCE_ICONS: Record<string, LucideIcon> = {
  files: Files,
  urls: Link2,
  text: Type,
  sitemap: Map,
  crawl: Globe,
  github: Github,
  notion: NotebookText,
  articles: FileText,
}

export function SourceTypeIcon({ type, className }: { type: string; className?: string }) {
  const Icon = SOURCE_ICONS[type] ?? FileText
  return <Icon className={cn('size-4', className)} aria-hidden />
}

export function SourceStatusBadge({ status, error }: { status: string; error?: string | null }) {
  if (status === 'syncing') {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="size-3 animate-spin" /> {t('knowledge.syncing')}
      </Badge>
    )
  }
  if (status === 'error') {
    const badge = (
      <Badge variant="destructive" className="gap-1">
        <AlertCircle className="size-3" /> {t('common.error')}
      </Badge>
    )
    if (!error) return badge
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{error}</TooltipContent>
      </Tooltip>
    )
  }
  return (
    <Badge variant="outline" className="gap-1">
      <CheckCircle2 className="size-3 text-emerald-500" /> {t('knowledge.idle')}
    </Badge>
  )
}

const DOC_STATUS: Record<
  string,
  { label: string; icon: LucideIcon; className: string; spin?: boolean }
> = {
  indexed: { label: 'Indexed', icon: CheckCircle2, className: 'text-emerald-600 dark:text-emerald-400' },
  processing: { label: 'Processing', icon: Loader2, className: 'text-blue-600 dark:text-blue-400', spin: true },
  pending: { label: 'Pending', icon: Clock, className: 'text-amber-600 dark:text-amber-400' },
  failed: { label: 'Failed', icon: XCircle, className: 'text-destructive' },
}

/** "Auto-sync: every Nm" pill for sources with a configured refresh interval. */
export function AutoSyncBadge({ minutes }: { minutes: number | null }) {
  if (minutes === null) return null
  return (
    <Badge variant="secondary" className="gap-1">
      <RefreshCw className="size-3" /> Auto-sync: every {minutes}m
    </Badge>
  )
}

/** Shown when a source has an encrypted secret (e.g. GitHub / Notion token) stored. */
export function CredentialsBadge({ hasSecrets }: { hasSecrets: boolean }) {
  if (!hasSecrets) return null
  return (
    <Badge variant="outline" className="gap-1">
      <KeyRound className="size-3" /> {t('knowledge.credentials_set')}
    </Badge>
  )
}

export function DocStatusBadge({ status, error }: { status: string; error?: string | null }) {
  const meta = DOC_STATUS[status] ?? DOC_STATUS.pending
  const Icon = meta.icon
  const badge = (
    <Badge variant="outline" className={cn('gap-1', meta.className)}>
      <Icon className={cn('size-3', meta.spin && 'animate-spin')} /> {meta.label}
    </Badge>
  )
  if (status === 'failed' && error) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{error}</TooltipContent>
      </Tooltip>
    )
  }
  return badge
}
