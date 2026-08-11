import { useRef, useState } from 'preact/hooks'
import { t } from '../../i18n'

/**
 * Message composer. Enter sends, Shift+Enter inserts a newline. Emits typing
 * signals (throttled by the controller). Text-only: the public widget API has
 * no file-upload endpoint, so attachments are intentionally omitted.
 */
export function Composer({
  onSend,
  onTyping,
  disabled,
}: {
  onSend: (text: string) => void
  onTyping: (isTyping: boolean) => void
  disabled?: boolean
}) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  const submit = (): void => {
    const value = text.trim()
    if (!value) return
    onSend(value)
    setText('')
    onTyping(false)
    if (ref.current) ref.current.style.height = 'auto'
  }

  return (
    <div class="sw-composer">
      <textarea
        ref={ref}
        class="sw-composer-input"
        placeholder={t('composer.placeholder')}
        rows={1}
        value={text}
        disabled={disabled}
        aria-label={t('composer.label')}
        onInput={(e) => {
          const el = e.currentTarget
          setText(el.value)
          onTyping(el.value.length > 0)
          el.style.height = 'auto'
          el.style.height = `${Math.min(el.scrollHeight, 120)}px`
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <button
        type="button"
        class="sw-send"
        aria-label={t('composer.send')}
        disabled={disabled || !text.trim()}
        onClick={submit}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path d="M3 20.5v-6l8-2.5-8-2.5v-6l19 8.5-19 8.5z" />
        </svg>
      </button>
    </div>
  )
}
