import { Clock, MousePointerClick } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
// The dashboard's shared markdown renderer (same feature set the widget ships).
import { Markdown } from '@/features/knowledge/components/markdown'

import { ACTION_KINDS, stepTypeLabel, type StepDraft } from '../lib'
import { t } from '@/i18n'

function Media({ draft }: { draft: StepDraft }) {
  if (!draft.mediaUrl.trim()) return null
  if (draft.mediaType === 'video') {
    return (
      <video
        src={draft.mediaUrl}
        className="mb-2 w-full rounded-md border"
        controls
        data-testid="preview-video"
      />
    )
  }
  return (
    <img
      src={draft.mediaUrl}
      alt=""
      className="mb-2 max-h-32 w-full rounded-md border object-cover"
    />
  )
}

function Bubble({
  draft,
  accent,
  index,
  total,
  showProgress,
}: {
  draft: StepDraft
  accent: string
  index: number
  total: number
  showProgress: boolean
}) {
  const advanceLabel =
    draft.advanceOn === 'button'
      ? 'Next'
      : draft.advanceOn === 'element_click'
        ? 'Waiting for a click on the element'
        : draft.advanceOn === 'input'
          ? 'Waiting for input'
          : `Auto-advances in ${draft.delayMs}ms`

  return (
    <div className="w-full rounded-lg border bg-popover p-3 text-popover-foreground shadow-sm">
      <Media draft={draft} />
      <p className="text-sm font-semibold">{draft.title || 'Untitled step'}</p>
      {draft.body ? (
        <Markdown content={draft.body} className="mt-1 text-xs text-muted-foreground" />
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">{t('tours.no_body_yet')}</p>
      )}
      <div className="mt-3 flex items-center gap-2">
        {showProgress ? (
          <span className="text-[11px] text-muted-foreground">
            {index + 1} of {total}
          </span>
        ) : null}
        <div className="flex-1" />
        {draft.advanceOn === 'button' ? (
          <span
            className="rounded-md px-2 py-1 text-[11px] font-medium text-white"
            // Accent is author-chosen at runtime — the one value Tailwind can't express.
            style={{ backgroundColor: accent }}
          >
            {advanceLabel}
          </span>
        ) : (
          <span className="text-[11px] text-muted-foreground">{advanceLabel}</span>
        )}
      </div>
    </div>
  )
}

/**
 * A static, in-editor mock of the selected step — deliberately NOT a live
 * preview of the customer's site (that is what the preview link is for).
 */
export function StepPreview({
  draft,
  accent,
  index,
  total,
  showProgress = true,
}: {
  draft: StepDraft | null
  accent: string
  index: number
  total: number
  showProgress?: boolean
}) {
  if (!draft) {
    return (
      <p className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
        {t('tours.select_a_step_to_preview_it')}
      </p>
    )
  }

  if (draft.type === 'action') {
    const kind = ACTION_KINDS.find((k) => k.value === draft.actionKind)?.label ?? draft.actionKind
    return (
      <div className="rounded-md border border-dashed p-4 text-xs" data-testid="step-preview">
        <Badge variant="secondary" className="mb-2 gap-1">
          <MousePointerClick aria-hidden /> {t('tours.driven_step')}
        </Badge>
        <p className="text-muted-foreground">
          In “do it for me” mode Stept performs this itself: <strong>{kind}</strong>
          {draft.actionKind === 'navigate' ? (
            <>
              {' '}
              → <code className="font-mono">{draft.actionUrl || '…'}</code>
            </>
          ) : (
            <>
              {' '}
              on <code className="font-mono">{draft.selector || '…'}</code>
            </>
          )}
          . In guided mode the user is asked to do it.
        </p>
      </div>
    )
  }

  if (draft.type === 'wait') {
    return (
      <div className="rounded-md border border-dashed p-4 text-xs" data-testid="step-preview">
        <Badge variant="secondary" className="mb-2 gap-1">
          <Clock aria-hidden /> {t('tours.hidden_step')}
        </Badge>
        <p className="text-muted-foreground">
          Nothing is shown. The player waits up to {draft.waitTimeoutMs}ms for{' '}
          <code className="font-mono">
            {draft.waitFor === 'url'
              ? draft.waitUrlPattern || '…'
              : draft.waitSelector || draft.selector || '…'}
          </code>
          , then moves on.
        </p>
      </div>
    )
  }

  if (draft.type === 'banner') {
    return (
      <div className="grid gap-2" data-testid="step-preview">
        <div
          className="flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
          style={{ borderColor: accent }}
        >
          <span className="font-semibold">{draft.title || 'Untitled banner'}</span>
          <span className="truncate text-muted-foreground">{draft.body}</span>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Docked full-width; never blocks the page.
        </p>
      </div>
    )
  }

  return (
    <div className="grid gap-2" data-testid="step-preview">
      <div className={draft.type === 'modal' ? 'rounded-md bg-muted/40 p-3' : undefined}>
        <Bubble
          draft={draft}
          accent={accent}
          index={index}
          total={total}
          showProgress={showProgress}
        />
      </div>
      <p className="text-[11px] text-muted-foreground">
        {draft.type === 'modal'
          ? 'Centered over a dimmed page.'
          : `${stepTypeLabel(draft.type)} anchored to ${draft.selector || 'the element'} (${draft.placement}).`}
      </p>
    </div>
  )
}
