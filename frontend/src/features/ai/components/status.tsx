/** Status pills for agents + runs, plus provider-kind display metadata. */

import { Loader2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import type { ProviderKind } from '../api'

export function AgentStatusBadge({ status }: { status: string }) {
  if (status === 'live') {
    return (
      <Badge className="gap-1 bg-emerald-600 text-white hover:bg-emerald-600/90">
        <span className="size-1.5 rounded-full bg-white" /> Live
      </Badge>
    )
  }
  if (status === 'off') {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        Off
      </Badge>
    )
  }
  return <Badge variant="secondary">Draft</Badge>
}

const RUN_STATUS: Record<string, { label: string; className: string; spin?: boolean }> = {
  queued: { label: 'Queued', className: 'text-muted-foreground' },
  running: { label: 'Running', className: 'text-blue-600 dark:text-blue-400', spin: true },
  awaiting_approval: { label: 'Awaiting approval', className: 'text-amber-600 dark:text-amber-400' },
  completed: { label: 'Completed', className: 'text-emerald-600 dark:text-emerald-400' },
  failed: { label: 'Failed', className: 'text-destructive' },
  handed_off: { label: 'Handed off', className: 'text-blue-600 dark:text-blue-400' },
  canceled: { label: 'Canceled', className: 'text-muted-foreground' },
}

export function RunStatusBadge({ status }: { status: string }) {
  const meta = RUN_STATUS[status] ?? { label: status, className: 'text-muted-foreground' }
  return (
    <Badge variant="outline" className={cn('gap-1', meta.className)}>
      {meta.spin ? <Loader2 className="size-3 animate-spin" /> : null}
      {meta.label}
    </Badge>
  )
}

export const PROVIDER_KINDS: { value: ProviderKind; label: string; needsBaseUrl?: boolean }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google (Gemini)' },
  { value: 'openai_compatible', label: 'OpenAI-compatible', needsBaseUrl: true },
  { value: 'ollama', label: 'Ollama', needsBaseUrl: true },
  { value: 'mock', label: 'Mock (testing)' },
]

export function providerKindLabel(kind: string): string {
  return PROVIDER_KINDS.find((k) => k.value === kind)?.label ?? kind
}
