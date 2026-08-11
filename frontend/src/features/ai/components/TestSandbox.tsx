/**
 * Live sandbox: runs the agent's SAVED config against an ephemeral message and
 * renders the resulting step trace + reply. With the mock provider you can drive
 * tool calls using directives like [[tool:search_knowledge {"query":"widget"}]].
 */

import { useMutation } from '@tanstack/react-query'
import { FlaskConical, Play, Sparkles } from 'lucide-react'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { useState } from 'react'

import { aiApi, type AgentTestResult } from '../api'
import { RunStatusBadge } from './status'
import { StepTrace } from './StepTrace'
import { t } from '@/i18n'

const PLACEHOLDER =
  'Ask the agent something…\n\nTip (mock provider): drive a tool with\n[[tool:search_knowledge {"query":"widget"}]]'

export function TestSandbox({ agentId }: { agentId: string }) {
  const [message, setMessage] = useState('')

  const mutation = useMutation({
    mutationFn: (): Promise<AgentTestResult> => aiApi.testAgent(agentId, { message: message.trim() }),
    onError: (e) => {
      if (!(e instanceof ApiError)) return
    },
  })

  function run() {
    if (message.trim()) mutation.mutate()
  }

  const result = mutation.data

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b p-3">
        <FlaskConical className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">{t('ai.test_sandbox')}</span>
        <Badge variant="outline" className="ml-auto text-[10px]">
          {t('ai.runs_saved_config_no_messages_sent')}
        </Badge>
      </div>

      <div className="border-b p-3">
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={PLACEHOLDER}
          aria-label={t('ai.sandbox_message')}
          rows={4}
          className="text-sm"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run()
          }}
        />
        <div className="mt-2 flex justify-end">
          <Button size="sm" onClick={run} disabled={!message.trim() || mutation.isPending}>
            {mutation.isPending ? <Spinner className="size-4" /> : <Play className="size-4" />}
            Run test
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {mutation.isError ? (
          <p className="text-sm text-destructive">
            {mutation.error instanceof ApiError ? mutation.error.message : 'Test failed'}
          </p>
        ) : null}

        {!result && !mutation.isPending ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <Sparkles className="size-6" />
            <p>Send a message to see the agent's reasoning trace and reply.</p>
          </div>
        ) : null}

        {result ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <RunStatusBadge status={result.status} />
            </div>

            {result.reply ? (
              <div className="rounded-lg border bg-muted/40 p-3">
                <p className="mb-1 text-xs font-medium text-muted-foreground">{t('common.reply')}</p>
                <p className="whitespace-pre-wrap text-sm">{result.reply}</p>
              </div>
            ) : null}

            {result.citations.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {result.citations.map((c) => (
                  <Badge key={c.n} variant="secondary" className="gap-1">
                    <span className="font-mono">[{c.n}]</span> {c.title}
                  </Badge>
                ))}
              </div>
            ) : null}

            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">{t('ai.trace')}</p>
              <StepTrace steps={result.steps} />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
