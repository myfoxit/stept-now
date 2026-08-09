/** Renders an AgentStep timeline: LLM calls, tool calls/results, approvals, replies. */

import {
  AlertTriangle,
  ArrowLeftRight,
  MessageSquare,
  MousePointerClick,
  Shield,
  ShieldCheck,
  ShieldQuestion,
  Sparkles,
  Users,
  Wrench,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import type { AgentStep } from '../api'

const KIND_META: Record<string, { label: string; icon: LucideIcon; className: string }> = {
  llm_call: { label: 'LLM call', icon: Sparkles, className: 'text-brand' },
  client_request: { label: 'Page op', icon: MousePointerClick, className: 'text-violet-500' },
  tool_call: { label: 'Tool call', icon: Wrench, className: 'text-blue-500' },
  tool_result: { label: 'Tool result', icon: ArrowLeftRight, className: 'text-blue-500' },
  approval_request: { label: 'Approval requested', icon: ShieldQuestion, className: 'text-amber-500' },
  approval_decision: { label: 'Approval decision', icon: ShieldCheck, className: 'text-amber-500' },
  final_reply: { label: 'Final reply', icon: MessageSquare, className: 'text-emerald-500' },
  guardrail: { label: 'Guardrail', icon: Shield, className: 'text-amber-500' },
  error: { label: 'Error', icon: AlertTriangle, className: 'text-destructive' },
  handoff: { label: 'Handoff', icon: Users, className: 'text-blue-500' },
}

function JsonBlock({ data }: { data: unknown }) {
  if (data == null || (typeof data === 'object' && Object.keys(data as object).length === 0)) {
    return null
  }
  return (
    <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted/60 p-2 text-xs">
      <code>{JSON.stringify(data, null, 2)}</code>
    </pre>
  )
}

function stepText(step: AgentStep): string | null {
  const out = step.output as Record<string, unknown> | null
  if (!out) return null
  const value = out.content ?? out.message ?? out.reply
  return typeof value === 'string' ? value : null
}

export function StepTrace({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">No steps recorded.</p>
  }
  return (
    <ol className="space-y-0">
      {steps.map((step, i) => {
        let meta = KIND_META[step.kind] ?? {
          label: step.kind,
          icon: Sparkles,
          className: 'text-muted-foreground',
        }
        // A deferred client call is either a DOM op or a page-registered app
        // action — different trust story, worth different words in the trace.
        if (
          step.kind === 'client_request' &&
          (step.output as Record<string, unknown> | null)?.op === 'action'
        ) {
          meta = { label: 'App action', icon: Zap, className: 'text-violet-500' }
        }
        const Icon = meta.icon
        const text = stepText(step)
        const last = i === steps.length - 1
        return (
          <li key={step.id} className="relative flex gap-3 pb-4">
            {!last ? (
              <span className="absolute left-[15px] top-8 bottom-0 w-px bg-border" aria-hidden />
            ) : null}
            <div
              className={cn(
                'flex size-8 shrink-0 items-center justify-center rounded-full border bg-background',
                meta.className
              )}
            >
              <Icon className="size-4" />
            </div>
            <div className="min-w-0 flex-1 pt-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{meta.label}</span>
                {step.name ? (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {step.name}
                  </Badge>
                ) : null}
                <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                  {step.input_tokens || step.output_tokens ? (
                    <span className="tabular-nums">
                      {step.input_tokens}↑ {step.output_tokens}↓
                    </span>
                  ) : null}
                  {step.latency_ms != null ? (
                    <span className="tabular-nums">{step.latency_ms} ms</span>
                  ) : null}
                </span>
              </div>
              {text ? (
                <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">{text}</p>
              ) : null}
              {step.kind === 'tool_call' || step.kind === 'client_request' ? (
                <JsonBlock data={step.input} />
              ) : null}
              {step.kind === 'tool_result' && !text ? <JsonBlock data={step.output} /> : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
