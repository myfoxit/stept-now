/**
 * Inline AI for the editor — the toolbar half of `POST /w/{ws}/ai/write`.
 *
 * Two modes, decided by what is selected:
 *
 * - **text selected** → rewrite commands (improve, shorten, expand, simplify, fix
 *   grammar, translate). The result REPLACES the selection.
 * - **nothing selected** → draft / outline from a prompt, grounded in the
 *   workspace knowledge base. The result is inserted at the cursor.
 *
 * The result is always applied as a single editor transaction so one undo takes
 * it back — an AI edit the author cannot cleanly reject is worse than no AI edit.
 */

import { Loader2, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { api, ws } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { t } from '@/i18n'

/** Commands that transform the selected text. */
const REWRITE_COMMANDS = [
  { command: 'improve', label: 'Improve writing' },
  { command: 'shorten', label: 'Make shorter' },
  { command: 'expand', label: 'Make longer' },
  { command: 'simplify', label: 'Simplify' },
  { command: 'fix', label: 'Fix spelling & grammar' },
] as const

interface WriteResponse {
  content: string
  citations: Array<{ n: number; title: string; url: string | null }>
}

export function AiMenu({
  disabled,
  /** The current selection as plain text ('' when nothing is selected). */
  selection,
  /** Whole document as markdown — context for a draft with no selection. */
  document,
  onReplaceSelection,
  onInsert,
}: {
  disabled: boolean
  selection: string
  document: string
  onReplaceSelection: (markdown: string) => void
  onInsert: (markdown: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [prompt, setPrompt] = useState('')
  const [language, setLanguage] = useState('')

  const hasSelection = selection.trim().length > 0

  async function run(command: string, extra: { language?: string; prompt?: string } = {}) {
    setBusy(command)
    try {
      const result = await api.post<WriteResponse>(ws('/ai/write'), {
        command,
        context: hasSelection ? selection : document,
        prompt: extra.prompt ?? null,
        language: extra.language ?? null,
      })
      const content = result.content.trim()
      if (!content) {
        toast.error(t('common.the_assistant_returned_nothing_try_again'))
        return
      }
      if (hasSelection) onReplaceSelection(content)
      else onInsert(content)
      if (result.citations.length) {
        toast.success(
          `Drafted from ${result.citations.length} knowledge source${
            result.citations.length === 1 ? '' : 's'
          }`
        )
      }
      setOpen(false)
      setPrompt('')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'The assistant could not help')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 px-2"
          disabled={disabled}
          aria-label={t('common.ask_ai')}
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          <span className="text-xs">AI</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 space-y-3 p-3">
        {hasSelection ? (
          <>
            <p className="text-xs text-muted-foreground">
              Rewriting the selected text ({selection.trim().length} characters).
            </p>
            <div className="grid gap-0.5">
              {REWRITE_COMMANDS.map(({ command, label }) => (
                <Button
                  key={command}
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="justify-start"
                  disabled={busy !== null}
                  onClick={() => void run(command)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <Separator />
            <div className="grid gap-1.5">
              <Label htmlFor="ai-translate-language" className="text-xs">
                {t('common.translate_into')}
              </Label>
              <div className="flex gap-1.5">
                <Input
                  id="ai-translate-language"
                  value={language}
                  placeholder={t('common.german')}
                  onChange={(event) => setLanguage(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && language.trim()) {
                      event.preventDefault()
                      void run('translate', { language: language.trim() })
                    }
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  disabled={busy !== null || !language.trim()}
                  onClick={() => void run('translate', { language: language.trim() })}
                >
                  Go
                </Button>
              </div>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {t('common.draft_from_your_knowledge_base_or')}
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="ai-draft-prompt" className="text-xs">
                {t('common.what_should_it_write')}
              </Label>
              <Input
                id="ai-draft-prompt"
                value={prompt}
                placeholder={t('common.a_short_guide_to_issuing_refunds')}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && prompt.trim()) {
                    event.preventDefault()
                    void run('draft', { prompt: prompt.trim() })
                  }
                }}
              />
            </div>
            <div className="flex gap-1.5">
              <Button
                type="button"
                size="sm"
                disabled={busy !== null || !prompt.trim()}
                onClick={() => void run('draft', { prompt: prompt.trim() })}
              >
                {t('common.draft')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy !== null || !prompt.trim()}
                onClick={() => void run('outline', { prompt: prompt.trim() })}
              >
                {t('common.outline')}
              </Button>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
