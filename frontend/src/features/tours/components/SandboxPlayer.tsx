import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Info, MonitorPlay, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Markdown } from '@/features/knowledge/components/markdown'
import { cn } from '@/lib/utils'

import { mediaSrc, type StepDraft } from '../lib'
import {
  buildSandboxDoc,
  SANDBOX_ATTR,
  sandboxCardSide,
  screenFor,
  snapshotWarnings,
  stepBox,
  type PageSnapshot,
} from '../sandbox'

/**
 * Load one stored replica.
 *
 * The media route is public and unauthenticated (the same route that serves
 * step screenshots to customer sites), so a bare `fetch` is correct here —
 * `api.get` would attach a workspace bearer the route neither wants nor reads.
 */
function useSnapshot(workspaceId: string, key: string | null) {
  return useQuery({
    queryKey: ['tours', workspaceId, 'sandbox', key],
    enabled: Boolean(key && workspaceId),
    staleTime: Infinity,
    queryFn: async (): Promise<PageSnapshot> => {
      const res = await fetch(mediaSrc(workspaceId, key!))
      if (!res.ok) throw new Error(`Snapshot ${res.status}`)
      return (await res.json()) as PageSnapshot
    },
  })
}

function Chrome({ url }: { url: string }) {
  return (
    <div className="flex items-center gap-2 border-b bg-muted/60 px-3 py-2">
      <div className="flex gap-1.5" aria-hidden>
        <span className="size-2.5 rounded-full bg-muted-foreground/30" />
        <span className="size-2.5 rounded-full bg-muted-foreground/30" />
        <span className="size-2.5 rounded-full bg-muted-foreground/30" />
      </div>
      <span className="truncate rounded bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
        {url || 'about:blank'}
      </span>
    </div>
  )
}

/** The guide card, positioned over the recorded element. */
function GuideCard({
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
  const box = stepBox(draft)
  const side = box ? sandboxCardSide(box) : 'bottom'

  const positioned = box
    ? {
        left: `${Math.min(Math.max(box.left, 2), 70)}%`,
        ...(side === 'bottom'
          ? { top: `calc(${box.top + box.height}% + 10px)` }
          : { bottom: `calc(${100 - box.top}% + 10px)` }),
      }
    : // No anchor (modal, banner, un-anchored step): centre it.
      { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }

  return (
    <div
      className="pointer-events-auto absolute z-10 w-[min(320px,70%)] rounded-lg border bg-popover p-3 text-popover-foreground shadow-lg"
      style={positioned}
      data-testid="sandbox-card"
    >
      <p className="text-sm font-semibold">{draft.title || 'Untitled step'}</p>
      {draft.body ? (
        <Markdown content={draft.body} className="mt-1 text-xs text-muted-foreground" />
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        {showProgress ? (
          <span className="text-[11px] text-muted-foreground">
            {index + 1} of {total}
          </span>
        ) : null}
        <div className="flex-1" />
        <span
          className="rounded-md px-2 py-1 text-[11px] font-medium text-white"
          // Author-chosen accent — not expressible as a Tailwind token.
          style={{ backgroundColor: accent }}
        >
          {draft.ctaLabel.trim() || (index === total - 1 ? 'Got it' : 'Next')}
        </span>
      </div>
    </div>
  )
}

/**
 * Replays a tour against the screens the recorder captured, instead of against
 * the live app.
 *
 * Two fidelities, decided per step by what was actually captured:
 *  - a DOM replica (sandbox capture on) — scrollable, hoverable, typeable;
 *  - the step screenshot — a still, but every tour ever recorded has one.
 *
 * The guide chrome is positioned from the element geometry recorded at capture
 * time rather than by querying the replica: the iframe is sandboxed to an
 * opaque origin precisely so its contents are unreachable, and a frozen screen
 * cannot have moved anyway.
 */
export function SandboxPlayer({
  steps,
  accent,
  workspaceId,
  showProgress = true,
}: {
  steps: StepDraft[]
  accent: string
  workspaceId: string
  showProgress?: boolean
}) {
  const [index, setIndex] = useState(0)
  // Steps can be deleted while the dialog is open.
  const bounded = Math.min(index, Math.max(steps.length - 1, 0))
  useEffect(() => {
    if (bounded !== index) setIndex(bounded)
  }, [bounded, index])

  const draft = steps[bounded]
  const snapshot = useSnapshot(workspaceId, draft?.sandboxKey ?? null)

  if (!draft) {
    return (
      <p className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
        Add a step to preview this tour in the sandbox.
      </p>
    )
  }

  const screenshot = draft.screenshotKey ? mediaSrc(workspaceId, draft.screenshotKey) : null
  const screen = screenFor(draft, snapshot.data, screenshot)
  const box = stepBox(draft)
  const warnings = screen.kind === 'replica' ? snapshotWarnings(screen.snapshot) : []

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={screen.kind === 'replica' ? 'default' : 'secondary'} className="gap-1">
          <MonitorPlay aria-hidden />
          {screen.kind === 'replica'
            ? 'Interactive replica'
            : screen.kind === 'screenshot'
              ? 'Screenshot'
              : 'No screen captured'}
        </Badge>
        <span className="text-xs text-muted-foreground">
          Step {bounded + 1} of {steps.length}
        </span>
        <div className="flex-1" />
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label="Previous step"
          disabled={bounded === 0}
          onClick={() => setIndex(bounded - 1)}
        >
          <ChevronLeft className="size-4" /> Back
        </Button>
        <Button
          type="button"
          size="sm"
          aria-label="Next step"
          disabled={bounded >= steps.length - 1}
          onClick={() => setIndex(bounded + 1)}
        >
          Next <ChevronRight className="size-4" />
        </Button>
      </div>

      <div className="overflow-hidden rounded-lg border bg-background shadow-sm">
        <Chrome url={screen.kind === 'replica' ? (screen.snapshot.url ?? '') : ''} />
        <div className="relative aspect-[16/10] w-full bg-muted/30" data-testid="sandbox-stage">
          {screen.kind === 'replica' ? (
            <iframe
              // Keying on the step forces a fresh document per screen; reusing
              // one iframe would leave the previous replica's scroll and focus.
              key={draft.key}
              title={`Sandbox replica for step ${bounded + 1}`}
              data-testid="sandbox-frame"
              className="size-full border-0 bg-white"
              // Empty sandbox: no scripts, no same-origin, no form submission.
              sandbox={SANDBOX_ATTR}
              referrerPolicy="no-referrer"
              srcDoc={buildSandboxDoc(screen.snapshot)}
            />
          ) : screen.kind === 'screenshot' ? (
            <img
              src={screen.src}
              alt={`Captured screen for step ${bounded + 1}`}
              className="size-full object-cover object-left-top"
            />
          ) : (
            <div className="flex size-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
              This step was authored by hand, so there is no screen to replay. Record it with the
              extension to capture one.
            </div>
          )}

          {snapshot.isLoading && draft.sandboxKey ? (
            <div className="absolute inset-0 grid place-items-center bg-background/60 text-xs text-muted-foreground">
              Loading the captured screen…
            </div>
          ) : null}

          {/* The element the step points at. */}
          {box ? (
            <span
              aria-hidden
              data-testid="sandbox-highlight"
              className={cn(
                'pointer-events-none absolute rounded-[3px] ring-2 ring-offset-2',
                'ring-primary ring-offset-background/20'
              )}
              style={{
                left: `${box.left}%`,
                top: `${box.top}%`,
                width: `${box.width}%`,
                height: `${box.height}%`,
              }}
            />
          ) : null}

          <div className="pointer-events-none absolute inset-0">
            <GuideCard
              draft={draft}
              accent={accent}
              index={bounded}
              total={steps.length}
              showProgress={showProgress}
            />
          </div>
        </div>
      </div>

      {snapshot.isError && draft.sandboxKey ? (
        <p className="flex items-start gap-1.5 text-xs text-destructive">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          The captured screen could not be loaded, so this step fell back to its screenshot.
        </p>
      ) : null}

      {warnings.map((warning) => (
        <p key={warning} className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {warning}
        </p>
      ))}

      {screen.kind === 'screenshot' && !draft.sandboxKey ? (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Turn on <span className="font-medium">Capture screens for sandbox</span> in the extension
          and re-record to make these screens interactive.
        </p>
      ) : null}
    </div>
  )
}
