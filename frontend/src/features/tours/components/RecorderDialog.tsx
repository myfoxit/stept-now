import { Check, Copy, Download, ExternalLink, Loader2, Puzzle } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Spinner } from '@/components/ui/spinner'

import { useExtensionRelease, useRecorderToken } from '../hooks'

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return ''
  const kb = bytes / 1024
  return kb < 1024 ? `${Math.round(kb)} KB` : `${(kb / 1024).toFixed(1)} MB`
}

/** A copyable one-liner. `min-w-0` is load-bearing: without it the flex child
 * refuses to shrink and long values (tokens, URLs) escape the dialog. */
function CopyField({
  value,
  label,
  testId,
}: {
  value: string
  label: string
  testId?: string
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard blocked — the value is selectable in the field */
    }
  }

  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
      <code className="min-w-0 flex-1 truncate font-mono text-xs" data-testid={testId}>
        {value}
      </code>
      <Button
        variant="ghost"
        size="icon"
        className="size-7 shrink-0"
        aria-label={label}
        onClick={copy}
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </Button>
    </div>
  )
}

function Step({ n, title, children }: { n: number; title: string; children?: React.ReactNode }) {
  return (
    <li className="flex min-w-0 gap-3">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
        {n}
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <p className="font-medium">{title}</p>
        {children}
      </div>
    </li>
  )
}

export function RecorderDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const release = useExtensionRelease()
  const recorder = useRecorderToken()
  const { mutate } = recorder

  useEffect(() => {
    if (open) mutate()
  }, [open, mutate])

  const token = recorder.data?.token
  const info = release.data
  const webStore = info?.web_store_url

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Record a tour with the Chrome extension</DialogTitle>
          <DialogDescription>
            Click through your product once — the recorder captures every step, its selectors and
            a screenshot, then hands the draft back here to edit and publish.
          </DialogDescription>
        </DialogHeader>

        <ol className="grid min-w-0 gap-5 py-2 text-sm">
          <Step n={1} title="Install the recorder">
            {release.isPending ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Spinner className="size-4" /> Checking for a build…
              </div>
            ) : webStore ? (
              <Button asChild size="sm">
                <a href={webStore} target="_blank" rel="noreferrer">
                  <Puzzle className="size-4" /> Add to Chrome
                  <ExternalLink className="size-3.5 opacity-70" />
                </a>
              </Button>
            ) : info?.available ? (
              <>
                <Button asChild size="sm">
                  <a href={info.download_url ?? undefined} download>
                    <Download className="size-4" /> Download the extension
                  </a>
                </Button>
                <p className="text-xs text-muted-foreground">
                  v{info.version} · {formatSize(info.size_bytes)} · unzip it, then load the folder
                  at <code className="font-mono">chrome://extensions</code> with{' '}
                  <span className="font-medium">Developer mode</span> on →{' '}
                  <span className="font-medium">Load unpacked</span>.
                </p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                No build found on the server. Run{' '}
                <code className="font-mono">pnpm --filter @stept/extension zip</code> and reload
                this dialog.
              </p>
            )}
          </Step>

          <Step n={2} title="Point it at this Stept">
            <p className="text-xs text-muted-foreground">
              Open the Stept side panel, then sign in with your Stept email and password using this
              server address:
            </p>
            <CopyField
              value={info?.api_base ?? ''}
              label="Copy server address"
              testId="recorder-api-base"
            />
          </Step>

          <Step n={3} title="Record">
            <p className="text-xs text-muted-foreground">
              Go to where your flow starts, hit <span className="font-medium">Start recording</span>{' '}
              in the panel, click through it, then save. The draft appears in Tours.
            </p>
          </Step>
        </ol>

        {/* `min-w-0` here and below: these are grid items, whose default
            `min-width: auto` lets the unbroken JWT widen the whole dialog. */}
        <details className="group min-w-0 border-t pt-3">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
            Can’t sign in from the extension? Use a paste-in token
          </summary>
          <div className="mt-3 min-w-0 space-y-2">
            <p className="text-xs text-muted-foreground">
              Paste this under <span className="font-medium">Advanced</span> on the extension’s sign
              in screen. It is workspace-scoped and expires in{' '}
              {recorder.data?.expires_days ?? 7} days.
            </p>
            {recorder.isPending ? (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Generating…
              </div>
            ) : token ? (
              <CopyField value={token} label="Copy token" testId="recorder-token" />
            ) : (
              <p className="text-xs text-destructive">Could not generate a token.</p>
            )}
          </div>
        </details>
      </DialogContent>
    </Dialog>
  )
}
