import type { UiMessage } from '../controller'
import { clockTime, initials } from '../format'
import { renderMarkdown } from '../md'

/** A single chat bubble. Visitor messages sit right; agent/AI/user left. */
export function MessageBubble({ message }: { message: UiMessage }) {
  const mine = message.direction === 'in'
  const system = message.author_type === 'system'
  const citations = message.meta?.citations ?? []

  if (system) {
    return (
      <div class="sw-activity">
        <span dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
      </div>
    )
  }

  return (
    <div class={`sw-row ${mine ? 'sw-row-mine' : 'sw-row-them'}`}>
      {!mine && (
        <div class="sw-avatar" aria-hidden="true">
          {initials(message.author_name || 'Agent')}
        </div>
      )}
      <div class="sw-bubble-wrap">
        <div
          class={`sw-bubble ${mine ? 'sw-bubble-mine' : 'sw-bubble-them'} ${
            message.failed ? 'sw-bubble-failed' : ''
          } ${message.pending ? 'sw-bubble-pending' : ''}`}
        >
          <div class="sw-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }} />
          {citations.length > 0 && (
            <div class="sw-citations">
              {citations.map((c) =>
                c.url ? (
                  <a
                    key={c.n}
                    class="sw-cite"
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={c.title || c.url}
                  >
                    [{c.n}] {c.title || 'Source'}
                  </a>
                ) : (
                  <span key={c.n} class="sw-cite">
                    [{c.n}] {c.title || 'Source'}
                  </span>
                ),
              )}
            </div>
          )}
        </div>
        <div class="sw-meta">
          {message.failed ? (
            <span class="sw-failed">Not delivered</span>
          ) : message.pending ? (
            <span>Sending…</span>
          ) : (
            <span>{clockTime(message.created_at)}</span>
          )}
        </div>
      </div>
    </div>
  )
}
