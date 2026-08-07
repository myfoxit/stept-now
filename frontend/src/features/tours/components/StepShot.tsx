import { useState } from 'react'

import { cn } from '@/lib/utils'

import { mediaSrc, targetBBox, type StepDraft } from '../lib'

/**
 * The screenshot the recorder took of a step, with the element it captured
 * boxed on top.
 *
 * The overlays are positioned in percentages against the recorded viewport
 * rather than burned into the image, so the marker stays crisp and correctly
 * aligned at any width the editor renders it at. Ported from the extension's
 * side-panel `Shot`, which does the same maths against the same descriptor.
 */
export function StepShot({
  draft,
  workspaceId,
  index,
  className,
}: {
  draft: StepDraft
  workspaceId: string
  index: number
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  if (!draft.screenshotKey || !workspaceId || failed) return null

  const box = targetBBox(draft.target)

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-md border bg-muted/30',
        className
      )}
      data-testid={`step-shot-${index}`}
    >
      <img
        src={mediaSrc(workspaceId, draft.screenshotKey)}
        alt={`Recorder screenshot for step ${index + 1}`}
        className="block w-full object-cover object-left-top"
        loading="lazy"
        onError={() => setFailed(true)}
      />
      {box ? (
        <>
          <span
            aria-hidden
            className="pointer-events-none absolute rounded-[3px] ring-2 ring-brand ring-offset-1 ring-offset-background/40"
            style={{
              left: `${(box.x / box.vw) * 100}%`,
              top: `${(box.y / box.vh) * 100}%`,
              width: `${(box.w / box.vw) * 100}%`,
              height: `${(box.h / box.vh) * 100}%`,
            }}
          />
          <span
            aria-hidden
            className="pointer-events-none absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand ring-2 ring-background"
            style={{
              left: `${((box.x + box.w / 2) / box.vw) * 100}%`,
              top: `${((box.y + box.h / 2) / box.vh) * 100}%`,
            }}
          />
        </>
      ) : null}
    </div>
  )
}
