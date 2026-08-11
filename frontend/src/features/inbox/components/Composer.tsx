/** Message composer: reply/note tabs, canned `/` picker, attachments, copilot. */

import { useRef, useState } from 'react'
import { Loader2, Paperclip, Send, Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'

import { sendRealtime } from '@/api/ws'
import { cn } from '@/lib/utils'
import { formatBytes } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  uploadFile,
  type AttachmentRef,
  type Canned,
  type Citation,
} from '@/features/inbox/api'
import { useCanned, useCopilot, useSendMessage } from '@/features/inbox/hooks'
import { t } from '@/i18n'

function fillPlaceholders(content: string, contactName: string, agentName: string): string {
  return content
    .replaceAll('{{contact.name}}', contactName || 'there')
    .replaceAll('{{agent.name}}', agentName || '')
}

const SLASH_RE = /(^|\s)\/([\w-]*)$/

export function Composer({
  conversationId,
  contactName,
  canWrite,
}: {
  conversationId: string
  contactName: string
  canWrite: boolean
}) {
  const [value, setValue] = useState('')
  const [visibility, setVisibility] = useState<'public' | 'note'>('public')
  const [attachments, setAttachments] = useState<AttachmentRef[]>([])
  const [uploading, setUploading] = useState(false)
  const [citations, setCitations] = useState<Citation[]>([])
  const [pickerQuery, setPickerQuery] = useState<string | null>(null)
  const [highlight, setHighlight] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const agentName = useAuthStore((s) => s.user?.name ?? '')
  const { data: canned = [] } = useCanned()
  const send = useSendMessage(conversationId)
  const copilot = useCopilot()

  const filtered =
    pickerQuery === null
      ? []
      : canned.filter((c) => c.shortcut.toLowerCase().startsWith(pickerQuery.toLowerCase()))
  const pickerOpen = pickerQuery !== null && filtered.length > 0

  function emitTyping() {
    sendRealtime({ type: 'typing', conversation_id: conversationId, is_typing: true })
    if (typingTimer.current) clearTimeout(typingTimer.current)
    typingTimer.current = setTimeout(
      () => sendRealtime({ type: 'typing', conversation_id: conversationId, is_typing: false }),
      3000
    )
  }

  function onChange(next: string) {
    setValue(next)
    if (next) emitTyping()
    const match = next.match(SLASH_RE)
    if (match) {
      setPickerQuery(match[2])
      setHighlight(0)
    } else {
      setPickerQuery(null)
    }
  }

  function insertCanned(response: Canned) {
    const filled = fillPlaceholders(response.content, contactName, agentName)
    const match = value.match(SLASH_RE)
    if (match) {
      const start = value.length - match[0].length
      setValue(value.slice(0, start) + match[1] + filled + ' ')
    } else {
      setValue((v) => v + filled)
    }
    setPickerQuery(null)
    textareaRef.current?.focus()
  }

  async function onFiles(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const uploaded = await uploadFile(file)
        setAttachments((prev) => [
          ...prev,
          {
            key: uploaded.key,
            name: uploaded.name,
            size: uploaded.size,
            content_type: uploaded.content_type,
          },
        ])
      }
    } catch {
      toast.error(t('inbox.upload_failed'))
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  function submit() {
    const content = value.trim()
    if (!content && attachments.length === 0) return
    send.mutate({ content: content || '(attachment)', visibility, attachments })
    setValue('')
    setAttachments([])
    setCitations([])
    setPickerQuery(null)
    sendRealtime({ type: 'typing', conversation_id: conversationId, is_typing: false })
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (pickerOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setHighlight((h) => Math.min(h + 1, filtered.length - 1))
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setHighlight((h) => Math.max(h - 1, 0))
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault()
        insertCanned(filtered[highlight])
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setPickerQuery(null)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  async function suggest() {
    const result = await copilot.mutateAsync(conversationId)
    setVisibility('public')
    setValue(result.content)
    setCitations(result.citations ?? [])
    textareaRef.current?.focus()
  }

  if (!canWrite) {
    return (
      <div className="border-t p-4 text-center text-sm text-muted-foreground">
        You don&apos;t have permission to reply to conversations.
      </div>
    )
  }

  return (
    <div className="border-t bg-background">
      <div className="flex items-center justify-between px-3 pt-2">
        <Tabs value={visibility} onValueChange={(v) => setVisibility(v as 'public' | 'note')}>
          <TabsList>
            <TabsTrigger value="public">{t('common.reply')}</TabsTrigger>
            <TabsTrigger value="note">{t('inbox.note')}</TabsTrigger>
          </TabsList>
        </Tabs>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={suggest}
          disabled={copilot.isPending}
          aria-label={t('inbox.suggest_reply')}
        >
          {copilot.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          Suggest reply
        </Button>
      </div>

      {citations.length ? (
        <div className="flex flex-wrap items-center gap-1 px-3 pt-2 text-xs text-muted-foreground">
          <span>Sources:</span>
          {citations.map((c) => (
            <span key={c.n} className="rounded border px-1.5 py-0.5">
              [{c.n}] {c.title}
            </span>
          ))}
        </div>
      ) : null}

      {attachments.length ? (
        <div className="flex flex-wrap gap-2 px-3 pt-2">
          {attachments.map((a) => (
            <span
              key={a.key}
              className="flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-1 text-xs"
            >
              <span className="max-w-[10rem] truncate">{a.name}</span>
              <span className="text-muted-foreground">{formatBytes(a.size)}</span>
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                onClick={() => setAttachments((prev) => prev.filter((x) => x.key !== a.key))}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="relative px-3 pb-3 pt-2">
        {pickerOpen ? (
          <div
            className="absolute bottom-full left-3 z-20 mb-1 w-72 overflow-hidden rounded-md border bg-popover shadow-md"
            data-testid="canned-picker"
          >
            <p className="border-b px-2 py-1 text-[11px] text-muted-foreground">{t('inbox.canned_responses')}</p>
            <ul className="max-h-56 overflow-y-auto py-1">
              {filtered.map((c, i) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault()
                      insertCanned(c)
                    }}
                    className={cn(
                      'flex w-full flex-col items-start gap-0.5 px-2 py-1.5 text-left text-sm hover:bg-accent',
                      i === highlight && 'bg-accent'
                    )}
                  >
                    <span className="font-medium">/{c.shortcut}</span>
                    <span className="line-clamp-1 text-xs text-muted-foreground">{c.content}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            visibility === 'note'
              ? 'Add an internal note… (teammates only)'
              : 'Write a reply… (type / for canned responses, Enter to send)'
          }
          aria-label={visibility === 'note' ? 'Internal note' : 'Reply'}
          className={cn(
            'min-h-20 resize-none',
            visibility === 'note' && 'bg-amber-50 dark:bg-amber-950/20'
          )}
        />

        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-1">
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => onFiles(e.target.files)}
              data-testid="file-input"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              aria-label={t('inbox.attach_file')}
            >
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <Paperclip className="size-4" />}
            </Button>
          </div>
          <Button type="button" onClick={submit} disabled={send.isPending} size="sm">
            <Send className="size-4" />
            {visibility === 'note' ? 'Add note' : 'Send'}
          </Button>
        </div>
      </div>
    </div>
  )
}
